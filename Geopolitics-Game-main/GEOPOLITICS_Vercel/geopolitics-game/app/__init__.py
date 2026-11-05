"""GEOPOLITICS -- Flask application factory."""
import logging
import os
import secrets
import threading
from concurrent.futures import ThreadPoolExecutor

import click
from flask import Flask, jsonify, render_template, request, session
from werkzeug.exceptions import HTTPException
from werkzeug.security import generate_password_hash

from .admin import bp as admin_bp
from .auth import EMAIL_RE, bp as auth_bp, csrf_token, is_admin, validate_credentials, validate_password
from .commentary import CommentaryService
from .config import Config
from .db import Database, to_oid
from .emailer import EmailService
from .engine import GameEngine
from .routes import api, pages

log = logging.getLogger(__name__)


def create_app(config=None, engine=None):
    # Static files live in app/static and are served by Flask at /static (default layout).
    # They are inside the Python package so Vercel's serverless build always bundles them.
    app = Flask(__name__)
    app.config.from_object(Config)
    if config:
        app.config.update(config)
    if app.config["SECRET_KEY"] == "dev-only-change-me" and not app.debug and not app.testing:
        app.logger.warning("SECRET_KEY is not set - using an insecure development key.")

    if app.config.get("SERVERLESS") and not app.config["MONGO_URI"]:
        raise RuntimeError("MONGO_URI is not set. Add it in Vercel > Project > Settings > Environment Variables.")
    if app.config.get("SERVERLESS"):
        # A serverless function is frozen as soon as the response is sent, so background threads would
        # never finish: narrate commentary and send e-mail inside the request instead.
        app.config["COMMENTARY_SYNC"] = True
        app.config["EMAIL_SYNC"] = True
    app.db = Database(app.config["MONGO_URI"], app.config["MONGO_DB_NAME"])
    app.engine = engine or GameEngine()
    app.commentary = CommentaryService(app.config["ANTHROPIC_API_KEY"], app.config["ANTHROPIC_MODEL"],
                                       gemini_key=app.config["GEMINI_API_KEY"],
                                       gemini_model=app.config["GEMINI_MODEL"])
    app.emailer = EmailService(app)
    app._commentary_pool = ThreadPoolExecutor(max_workers=2)

    def narrate_and_store(game_id, records):
        """Runs off the request thread. Describes finished battles; never touches game state."""
        for rec in records[-5:]:
            try:
                out = app.commentary.narrate(rec)
                entry = {"battle_no": rec["battle_no"], "round": rec["round"], **out}
                app.db.battles.set_commentary(game_id, rec["battle_no"], out)
                app.db.games.col.update_one({"_id": game_id}, {"$push": {"commentary": {"$each": [entry], "$slice": -30}}})
            except Exception:
                log.exception("commentary storage failed")

    def narrate_phase_and_store(game_id, ctx):
        """Live commentary for one phase of a battle still in progress (human battles)."""
        try:
            out = app.commentary.narrate_phase(ctx)
            entry = {"battle_no": ctx["battle_no"], "round": ctx["round"], "phase": ctx["phase"]["no"],
                     "live": True, **out}
            app.db.games.col.update_one({"_id": game_id}, {"$push": {"commentary": {"$each": [entry], "$slice": -30}}})
        except Exception:
            log.exception("live commentary storage failed")

    def schedule_phase_commentary(game_id, ctx):
        if app.config["COMMENTARY_SYNC"]:
            narrate_phase_and_store(game_id, ctx)
        else:
            app._commentary_pool.submit(narrate_phase_and_store, game_id, ctx)

    app.schedule_phase_commentary = schedule_phase_commentary

    def schedule_commentary(game_id, records):
        if app.config["COMMENTARY_SYNC"]:
            narrate_and_store(game_id, records)
        else:
            app._commentary_pool.submit(narrate_and_store, game_id, records)

    app.schedule_commentary = schedule_commentary

    app.register_blueprint(auth_bp)
    app.register_blueprint(pages)
    app.register_blueprint(api)
    app.register_blueprint(admin_bp)
    app.jinja_env.globals["csrf_token"] = csrf_token

    @app.context_processor
    def nav_context():
        from flask import g
        user = getattr(g, "user", None)
        ctx = {"is_admin": is_admin(user), "nav_game": None, "nav_watch": None}
        va = request.view_args or {}
        if va.get("code"):
            ctx["nav_watch"] = va["code"]
        elif va.get("game_id"):
            ctx["nav_game"] = va["game_id"]
        return ctx

    @app.after_request
    def security_headers(resp):
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        resp.headers.setdefault("Content-Security-Policy",
                                "default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:")
        return resp

    @app.errorhandler(404)
    def not_found(_):
        if request.path.startswith("/api/"):
            return jsonify(error="Not found."), 404
        return render_template("error.html", message="That page could not be found."), 404

    @app.errorhandler(Exception)
    def server_error(exc):
        if isinstance(exc, HTTPException):
            if request.path.startswith("/api/"):
                return jsonify(error=exc.description), exc.code
            return render_template("error.html", message=exc.description), exc.code
        log.exception("unhandled error")
        try:
            app.db.errors.add(path=request.path, method=request.method, error=f"{type(exc).__name__}: {exc}"[:500])
            app.emailer.alert_admin("GEOPOLITICS: server error", f"{type(exc).__name__} at {request.path}",
                                    f"error:{type(exc).__name__}:{request.path}")
        except Exception:
            pass
        if request.path.startswith("/api/"):
            return jsonify(error="Something went wrong on our side. Please try again."), 500
        return render_template("error.html", message="Something went wrong on our side."), 500

    @app.route("/healthz")
    def healthz():
        return {"status": "ok"}

    @app.post("/api/cron/process-emails")
    def cron_process_emails():
        """Vercel Cron Job: deliver due/retrying e-mail jobs. Requires CRON_SECRET."""
        secret = app.config.get("CRON_SECRET")
        if not secret:
            return jsonify(error="CRON_SECRET is not configured."), 503
        sent = request.headers.get("Authorization", "")
        if not secrets.compare_digest(sent, f"Bearer {secret}"):
            return jsonify(error="Unauthorized."), 401
        return jsonify(processed=app.emailer.process_due())

    # ---- trusted server-side operations (no web endpoint can do these) --------------------
    @app.cli.command("create-user")
    @click.argument("username")
    @click.argument("password")
    @click.option("--email", required=True)
    @click.option("--verified/--unverified", default=True, help="Mark the e-mail as verified (trusted operator step).")
    def create_user(username, password, email, verified):
        """Create an account (e.g. the demo/test user for reviewers)."""
        err = validate_credentials(username, password)
        if not err and not EMAIL_RE.match(email):
            err = "Invalid e-mail address."
        if err:
            raise click.ClickException(err)
        user = app.db.users.create(username.lower(), email.lower(), generate_password_hash(password))
        if verified:
            app.db.users.set(user["_id"], email_verified=True)
        click.echo(f"Created user {username.lower()} <{email.lower()}> verified={verified}")

    @app.cli.command("grant-admin")
    @click.argument("email")
    def grant_admin(email):
        """One-time provisioning: give an EXISTING account with a VERIFIED e-mail the admin role."""
        user = app.db.users.by_email(email.lower())
        if not user:
            raise click.ClickException("No account with that e-mail. The owner must sign up first.")
        if not user.get("email_verified"):
            raise click.ClickException("That e-mail is not verified yet. Click the confirmation link first "
                                       "(or, once you have confirmed ownership out-of-band, run `flask verify-email`).")
        app.db.users.set(user["_id"], role="admin")
        app.db.audit.add(actor="cli", action="admin.grant", target=user["username"])
        click.echo(f"{user['username']} is now an administrator.")

    @app.cli.command("seed-accounts")
    @click.argument("emails", nargs=-1)
    @click.option("--from-csv", "from_csv", type=click.Path(exists=True, dir_okay=False),
                  help="CSV with columns email,password (pre-issued passwords). Never commit this file.")
    @click.option("--admin", "admins", multiple=True, help="E-mail(s) among EMAILS to give the admin role.")
    @click.option("--reset-password", is_flag=True, help="Issue a new password for accounts that already exist.")
    @click.option("--out", type=click.Path(dir_okay=False), help="Also write the credentials to this CSV file.")
    def seed_accounts(emails, admins, reset_password, out, from_csv):
        """Trusted operator step: provision reviewer/owner accounts with generated passwords.

        Accounts are created verified (the operator vouches for the addresses). Admin is granted only to
        addresses passed with --admin. Passwords are printed once and stored only as hashes."""
        import csv
        import re
        import secrets
        import string

        def username_for(email):
            base = re.sub(r"[^A-Za-z0-9_]", "_", email.split("@")[0]).strip("_").lower()[:20] or "user"
            base = base if len(base) >= 3 else (base + "_usr")[:20]
            name, n = base, 1
            while app.db.users.by_username(name) and (app.db.users.by_username(name).get("email") != email):
                n += 1
                name = f"{base[:17]}{n}"
            return name

        def new_password():
            alphabet = string.ascii_letters + string.digits
            while True:
                pw = "".join(secrets.choice(alphabet) for _ in range(14))
                if not validate_password(pw) and any(c.isupper() for c in pw) and any(c.islower() for c in pw):
                    return pw

        given = {}
        if from_csv:
            with open(from_csv, newline="") as fh:
                for row in csv.DictReader(fh):
                    em, pw = (row.get("email") or "").strip().lower(), row.get("password") or ""
                    err = validate_password(pw)
                    if err:
                        raise click.ClickException(f"{em}: {err}")
                    given[em] = pw
        emails = list(dict.fromkeys([e.lower().strip() for e in emails] + list(given)))
        if not emails:
            raise click.ClickException("Give at least one e-mail (or --from-csv).")
        admins = {a.lower() for a in admins}
        missing = admins - {e.lower() for e in emails}
        if missing:
            raise click.ClickException(f"--admin address(es) not in the list: {', '.join(sorted(missing))}")
        rows = []
        for email in (e.lower().strip() for e in emails):
            if not EMAIL_RE.match(email):
                raise click.ClickException(f"Invalid e-mail address: {email}")
            role = "admin" if email in admins else "player"
            user = app.db.users.by_email(email)
            password = None
            if user is None:
                password = given.get(email) or new_password()
                user = app.db.users.create(username_for(email), email, generate_password_hash(password), role=role)
                action = "created"
            elif reset_password:
                password = given.get(email) or new_password()
                app.db.users.set(user["_id"], password_hash=generate_password_hash(password), failed_logins=0,
                                 locked_until=None)
                action = "password reset"
            else:
                action = "already exists (password unchanged)"
            app.db.users.set(user["_id"], email_verified=True, role=role, disabled=False)
            app.db.audit.add(actor="cli", action="account.seed", target=user["username"], role=role, result=action)
            rows.append({"email": email, "username": user["username"], "password": password or "", "role": role,
                         "status": action})
            click.echo(f"{email:34} {user['username']:20} {role:7} {password or '-':16} {action}")
        if out:
            with open(out, "w", newline="") as fh:
                w = csv.DictWriter(fh, fieldnames=["email", "username", "password", "role", "status"])
                w.writeheader()
                w.writerows(rows)

    @app.cli.command("verify-email")
    @click.argument("email")
    def verify_email(email):
        """Operator override: mark an account's e-mail verified after confirming ownership out-of-band."""
        user = app.db.users.by_email(email.lower())
        if not user:
            raise click.ClickException("No account with that e-mail.")
        app.db.users.set(user["_id"], email_verified=True)
        app.db.audit.add(actor="cli", action="email.verify", target=user["username"])
        click.echo("Verified.")

    @app.cli.command("test-email")
    @click.argument("to")
    def test_email(to):
        """Send one test e-mail right now and print the exact result/error."""
        prov = app.emailer._provider()
        click.echo(f"Provider: {prov or 'NONE (set SMTP_HOST/SMTP_USER/SMTP_PASS in .env)'}")
        if not prov:
            raise click.ClickException("No e-mail provider configured.")
        job = {"to": to, "subject": "GEOPOLITICS test e-mail", "html": "<p>It works.</p>", "text": "It works."}
        try:
            app.emailer._send(job)
        except Exception as exc:
            raise click.ClickException(f"FAILED: {type(exc).__name__}: {exc}")
        click.echo(f"Sent to {to}. Check the inbox (and spam).")

    @app.cli.command("process-emails")
    def process_emails():
        """Deliver due/retrying e-mail jobs (run from a cron job)."""
        click.echo(f"processed {app.emailer.process_due()} job(s)")

    @app.cli.command("db-status")
    def db_status():
        """Print live database connection evidence."""
        click.echo(app.db.status())

    return app
