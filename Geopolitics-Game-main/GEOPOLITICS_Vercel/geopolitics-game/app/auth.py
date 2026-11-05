"""Registration, login, sessions, e-mail verification, password reset, CSRF, rate limiting and
the authorisation decorators. Roles are stored server-side in the database only."""
from __future__ import annotations

import re
import secrets
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from functools import wraps

from flask import (Blueprint, current_app, flash, g, jsonify, redirect, render_template,
                   request, session, url_for)
from pymongo.errors import DuplicateKeyError
from werkzeug.security import check_password_hash, generate_password_hash

bp = Blueprint("auth", __name__)

USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{3,20}$")
EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,255}\.[^@\s]{2,}$")
LOCK_AFTER = 5
LOCK_MINUTES = 5
_DUMMY_HASH = generate_password_hash("not-a-real-password")   # constant-time-ish unknown-user check


def validate_credentials(username, password):
    if not USERNAME_RE.match(username or ""):
        return "Username must be 3-20 characters: letters, numbers or underscore."
    return validate_password(password)


def validate_password(password):
    if not isinstance(password, str) or not (8 <= len(password) <= 128):
        return "Password must be 8-128 characters."
    if not (re.search(r"[A-Za-z]", password) and re.search(r"\d", password)):
        return "Password must contain at least one letter and one number."
    return None


# ---------------------------------------------------------------------------
# Rate limiting (per client address, in-process; adequate for a single small deployment)
# ---------------------------------------------------------------------------
_hits = defaultdict(deque)


def rate_limit(name, limit, window):
    def deco(view):
        @wraps(view)
        def wrapper(*a, **kw):
            if current_app.config.get("RATE_LIMIT_ENABLED", True) and request.method == "POST":
                key, t = (name, request.remote_addr), time.monotonic()
                q = _hits[key]
                while q and t - q[0] > window:
                    q.popleft()
                if len(q) >= limit:
                    if request.path.startswith("/api/"):
                        return jsonify(error="Too many requests. Slow down."), 429
                    flash("Too many attempts. Please wait a moment.", "error")
                    return render_template("error.html", message="Too many attempts. Please wait a moment."), 429
                q.append(t)
            return view(*a, **kw)
        return wrapper
    return deco


# ---------------------------------------------------------------------------
# CSRF + current user
# ---------------------------------------------------------------------------
def csrf_token():
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(24)
    return session["csrf"]


@bp.before_app_request
def load_user_and_check_csrf():
    g.user = None
    uid = session.get("uid")
    if uid:
        g.user = current_app.db.users.by_id(uid)
        if g.user is None or g.user.get("disabled"):
            session.clear()
            g.user = None
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        # Vercel Cron Jobs cannot send session CSRF tokens; that endpoint authenticates
        # itself with the CRON_SECRET bearer token instead.
        if request.path == "/api/cron/process-emails":
            return
        sent = request.headers.get("X-CSRF-Token") or request.form.get("csrf_token")
        expected = session.get("csrf")
        if not expected or not sent or not secrets.compare_digest(sent, expected):
            if request.path.startswith("/api/"):
                return jsonify(error="Invalid or missing CSRF token. Reload the page."), 400
            flash("Your session expired. Please try again.", "error")
            return redirect(url_for("auth.login"))


def is_admin(user):
    """Role is read from the database on every request (never from the client)."""
    return bool(user and user.get("role") == "admin" and user.get("email_verified") and not user.get("disabled"))


def login_required(view):
    @wraps(view)
    def wrapper(*a, **kw):
        if g.user is None:
            if request.path.startswith("/api/"):
                return jsonify(error="Please log in."), 401
            return redirect(url_for("auth.login", next=request.path))
        return view(*a, **kw)
    return wrapper


def admin_required(view):
    @wraps(view)
    def wrapper(*a, **kw):
        if g.user is None:
            if request.path.startswith("/api/"):
                return jsonify(error="Please log in."), 401
            return redirect(url_for("auth.login", next=request.path))
        if not is_admin(g.user):
            if request.path.startswith("/api/"):
                return jsonify(error="Administrator access required."), 403
            return render_template("error.html", message="You do not have access to this page."), 403
        return view(*a, **kw)
    return wrapper


def _issue_verification(user):
    raw = secrets.token_urlsafe(32)
    current_app.db.tokens.issue(user["_id"], "verify", raw, ttl_hours=48)
    link = f"{current_app.config['PUBLIC_BASE_URL'].rstrip('/')}/verify/{raw}"
    current_app.emailer.queue("verify", user, f"verify:{user['_id']}:{raw[:12]}", {"link": link})


# ---------------------------------------------------------------------------
# Views
# ---------------------------------------------------------------------------
@bp.route("/register", methods=["GET", "POST"])
@rate_limit("register", 10, 600)
def register():
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        email = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password") or ""
        confirm = request.form.get("confirm") or ""
        error = validate_credentials(username, password)
        if not error and not EMAIL_RE.match(email):
            error = "Enter a valid e-mail address."
        if not error and password != confirm:
            error = "Passwords do not match."
        user = None
        if not error:
            try:
                user = current_app.db.users.create(username.lower(), email, generate_password_hash(password))
            except DuplicateKeyError:
                error = "That username or e-mail is already registered."
        if error:
            flash(error, "error")
            return render_template("register.html", username=username, email=email), 400
        _issue_verification(user)
        session.clear()
        session["uid"] = str(user["_id"])
        flash("Account created. We sent a confirmation link to your e-mail address.", "ok")
        return redirect(url_for("pages.headquarters"))
    return render_template("register.html", username="", email="")


@bp.route("/login", methods=["GET", "POST"])
@rate_limit("login", 30, 300)
def login():
    if request.method == "POST":
        username = (request.form.get("username") or "").strip().lower()
        password = request.form.get("password") or ""
        users = current_app.db.users
        if USERNAME_RE.match(username):
            user = users.by_username(username)
        elif EMAIL_RE.match(username):
            user = users.by_email(username)                 # sign in with e-mail address as well
        else:
            user = None
        now = datetime.now(timezone.utc)
        if user and user.get("locked_until") and _aware(user["locked_until"]) > now:
            flash("Too many failed attempts. Try again in a few minutes.", "error")
            return render_template("login.html", username=username), 429
        ok = check_password_hash(user["password_hash"] if user else _DUMMY_HASH, password)
        if user and ok and not user.get("disabled"):
            users.clear_failures(user["_id"])
            session.clear()
            session["uid"] = str(user["_id"])
            nxt = request.args.get("next", "")
            return redirect(nxt if nxt.startswith("/") and not nxt.startswith("//") else url_for("pages.headquarters"))
        if user and not ok:
            users.record_failure(user["_id"], LOCK_AFTER, now + timedelta(minutes=LOCK_MINUTES))
        flash("Incorrect username or password.", "error")
        return render_template("login.html", username=username), 401
    return render_template("login.html", username="")


def _aware(dt):
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


@bp.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("pages.home"))


@bp.route("/verify/<token>")
def verify(token):
    uid = current_app.db.tokens.consume(token, "verify")
    user = current_app.db.users.by_id(uid) if uid else None
    if not user:
        return render_template("error.html", message="This confirmation link is invalid or has expired."), 400
    if not user.get("email_verified"):
        current_app.db.users.set(user["_id"], email_verified=True)
        current_app.emailer.queue("welcome", user, f"welcome:{user['_id']}")
    flash("E-mail confirmed. Welcome aboard.", "ok")
    return redirect(url_for("pages.headquarters") if g.user else url_for("auth.login"))


@bp.route("/verify/resend", methods=["POST"])
@login_required
@rate_limit("resend", 3, 3600)
def resend_verification():
    if g.user.get("email_verified"):
        flash("Your e-mail is already confirmed.", "ok")
    else:
        _issue_verification(g.user)
        flash("Confirmation e-mail queued.", "ok")
    return redirect(request.referrer or url_for("pages.headquarters"))


@bp.route("/forgot", methods=["GET", "POST"])
@rate_limit("forgot", 5, 3600)
def forgot():
    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        user = current_app.db.users.by_email(email) if EMAIL_RE.match(email) else None
        if user and not user.get("disabled"):
            raw = secrets.token_urlsafe(32)
            current_app.db.tokens.issue(user["_id"], "reset", raw, ttl_hours=2)
            link = f"{current_app.config['PUBLIC_BASE_URL'].rstrip('/')}/reset/{raw}"
            current_app.emailer.queue("reset", user, f"reset:{user['_id']}:{raw[:12]}", {"link": link})
        flash("If that address is registered, a reset link is on its way.", "ok")   # no account enumeration
        return redirect(url_for("auth.login"))
    return render_template("forgot.html")


@bp.route("/reset/<token>", methods=["GET", "POST"])
def reset(token):
    if request.method == "POST":
        password = request.form.get("password") or ""
        error = validate_password(password)
        if not error and password != (request.form.get("confirm") or ""):
            error = "Passwords do not match."
        if error:
            flash(error, "error")
            return render_template("reset.html", token=token), 400
        uid = current_app.db.tokens.consume(token, "reset")
        if not uid:
            return render_template("error.html", message="This reset link is invalid or has expired."), 400
        current_app.db.users.set(uid_obj(uid), password_hash=generate_password_hash(password),
                                 failed_logins=0, locked_until=None)
        flash("Password updated. You can log in now.", "ok")
        return redirect(url_for("auth.login"))
    return render_template("reset.html", token=token)


def uid_obj(uid):
    from .db import to_oid
    return to_oid(uid)
