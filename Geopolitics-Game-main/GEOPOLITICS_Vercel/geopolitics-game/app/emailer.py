"""Transactional e-mail: queue, deliver (Resend), retry with back-off, de-duplicate, log.

E-mail problems never block gameplay: every public method swallows its own errors. Without
RESEND_API_KEY jobs are recorded as "skipped" (visible in the admin console) so nothing is
silently lost and they can be re-queued once a provider is configured.
"""
from __future__ import annotations

import html
import logging
import smtplib
from email.message import EmailMessage
from email.utils import formataddr, parseaddr
from concurrent.futures import ThreadPoolExecutor

import requests
from itsdangerous import BadSignature, URLSafeTimedSerializer

from .models import now

log = logging.getLogger(__name__)
RESEND_URL = "https://api.resend.com/emails"


class TemporaryError(Exception):
    pass


class PermanentError(Exception):
    pass


def _layout(title, body_html):
    return (f'<div style="background:#0b1220;padding:24px;font-family:Arial,sans-serif;color:#e6ebf5">'
            f'<div style="max-width:520px;margin:auto;background:#121c30;border:1px solid #243452;'
            f'border-radius:10px;padding:24px"><div style="letter-spacing:.2em;font-weight:700;color:#d9ab43">'
            f'GEOPOLITICS</div><h2 style="margin:16px 0 8px;color:#fff">{html.escape(title)}</h2>{body_html}'
            f'</div></div>')


def _button(url, label):
    return (f'<p><a href="{html.escape(url)}" style="background:#d9ab43;color:#10151f;padding:10px 18px;'
            f'border-radius:6px;text-decoration:none;font-weight:700">{html.escape(label)}</a></p>')


TEMPLATES = {
    "welcome": lambda c: ("Welcome to GEOPOLITICS",
        _layout("Welcome, commander", f"<p>Your account <b>{html.escape(c['username'])}</b> is ready.</p>"
                + _button(c["base_url"] + "/new", "Start a campaign")),
        f"Welcome to GEOPOLITICS, {c['username']}. Start a campaign: {c['base_url']}/new"),
    "verify": lambda c: ("Confirm your e-mail address",
        _layout("Confirm your e-mail", "<p>Confirm this address to secure your account.</p>"
                + _button(c["link"], "Confirm e-mail") + "<p style='color:#8fa0bf'>The link expires in 48 hours.</p>"),
        f"Confirm your e-mail: {c['link']}"),
    "reset": lambda c: ("Reset your GEOPOLITICS password",
        _layout("Password reset", "<p>Use the button below to choose a new password.</p>"
                + _button(c["link"], "Reset password")
                + "<p style='color:#8fa0bf'>The link expires in 2 hours. If you did not ask for this, ignore it.</p>"),
        f"Reset your password: {c['link']}"),
    "new_game": lambda c: ("Campaign started",
        _layout("Campaign started", f"<p>You are commanding <b>{html.escape(c['nation'])}</b>.</p>"
                + _button(c["link"], "Open the War Room")),
        f"Campaign started as {c['nation']}: {c['link']}"),
    "game_finished": lambda c: ("Campaign finished",
        _layout("Campaign finished", f"<p>{html.escape(c['summary'])}</p>"
                + _button(c["link"], "View the campaign")),
        f"{c['summary']} {c['link']}"),
    "admin_alert": lambda c: (c["subject"], _layout("Operational alert", f"<p>{html.escape(c['body'])}</p>"),
                              c["body"]),
}
OPTIONAL = {"new_game", "game_finished"}


class EmailService:
    def __init__(self, app):
        self.app = app
        self.pool = ThreadPoolExecutor(max_workers=2)
        self.signer = URLSafeTimedSerializer(app.config["SECRET_KEY"], salt="unsubscribe")

    # ---- public API -------------------------------------------------------
    @property
    def db(self):
        return self.app.db

    def unsubscribe_token(self, user_id, pref):
        return self.signer.dumps({"uid": str(user_id), "pref": pref})

    def read_unsubscribe_token(self, token):
        try:
            return self.signer.loads(token, max_age=60 * 60 * 24 * 365)
        except BadSignature:
            return None

    def queue(self, template, user, dedupe_key, ctx=None, pref=None):
        """Queue an e-mail for `user` (a user document). Never raises."""
        try:
            if not user or not user.get("email"):
                return None
            if pref and not self.db.prefs.get(user["_id"]).get(pref, False):
                return None
            base = self.app.config["PUBLIC_BASE_URL"].rstrip("/")
            ctx = {"base_url": base, "username": user["username"], **(ctx or {})}
            subject, body, text = TEMPLATES[template](ctx)
            if template in OPTIONAL and pref:
                url = f"{base}/unsubscribe/{self.unsubscribe_token(user['_id'], pref)}"
                body += f'<p style="font-size:12px;color:#8fa0bf">Don\'t want these? <a href="{url}">Unsubscribe</a>.</p>'
                text += f"\nUnsubscribe: {url}"
            return self._enqueue(template, user["email"], str(user["_id"]), dedupe_key, subject, body, text)
        except Exception:
            log.exception("could not queue e-mail %s", template)
            return None

    def alert_admin(self, subject, body, key):
        """Opt-in, throttled (one per key per hour) and de-duplicated owner alert."""
        try:
            if not self.app.config["ADMIN_ALERTS_ENABLED"]:
                return None
            bucket = now().strftime("%Y%m%d%H")
            subject_, html_, text = TEMPLATES["admin_alert"]({"subject": subject, "body": body})
            return self._enqueue("admin_alert", self.app.config["ADMIN_ALERT_EMAIL"], None,
                                 f"admin:{key}:{bucket}", subject_, html_, text)
        except Exception:
            log.exception("could not queue admin alert")
            return None

    def process_due(self):
        """Deliver due jobs (called by `flask process-emails`, the admin button or a cron)."""
        done = 0
        for job in self.db.emails.due():
            self.deliver(job)
            done += 1
        return done

    def requeue(self, job_id):
        job = self.db.emails.get(job_id)
        if job and job["status"] in ("failed", "skipped", "retry"):
            self.db.emails.mark(job["_id"], status="queued", attempts=0, next_attempt_at=now())
            self._dispatch(self.db.emails.get(job_id))
            return True
        return False

    # ---- internals --------------------------------------------------------
    def _enqueue(self, template, to, user_id, dedupe_key, subject, body, text):
        job = self.db.emails.create({"template": template, "to": to, "user_id": user_id,
                                     "dedupe_key": dedupe_key, "subject": subject, "html": body, "text": text})
        if job:
            self._dispatch(job)
        return job

    def _dispatch(self, job):
        if self.app.config["EMAIL_SYNC"]:
            self.deliver(job)
        else:
            self.pool.submit(self.deliver, job)

    def deliver(self, job):
        """One delivery attempt. Updates the job; never raises."""
        try:
            if not self._provider():
                log.warning("E-mail NOT sent to %s: no provider configured. Set SMTP_HOST, SMTP_USER and SMTP_PASS "
                            "(or RESEND_API_KEY) in .env and restart.", job["to"])
                self.db.emails.mark(job["_id"], status="skipped", last_error="No e-mail provider configured")
                return False
            provider_id = self._send(job)
            self.db.emails.mark(job["_id"], status="sent", provider_id=provider_id, last_error=None,
                                sent_at=now(), attempts=job["attempts"] + 1)
            return True
        except PermanentError as exc:
            log.warning("E-mail to %s FAILED permanently: %s", job["to"], exc)
            self.db.emails.mark(job["_id"], status="failed", last_error=str(exc)[:300], attempts=job["attempts"] + 1)
        except Exception as exc:        # temporary: retry with back-off, limited attempts
            log.warning("E-mail to %s failed (will retry): %s: %s", job["to"], type(exc).__name__, exc)
            self.db.emails.attempt_failed(job, f"{type(exc).__name__}: {exc}")
        return False

    def _provider(self):
        cfg = self.app.config
        if cfg["RESEND_API_KEY"]:
            return "resend"
        if cfg.get("SMTP_HOST") and cfg.get("SMTP_USER") and cfg.get("SMTP_PASS"):
            return "smtp"
        return None

    def _send_smtp(self, job):
        cfg = self.app.config
        user, pw = cfg["SMTP_USER"].strip(), cfg["SMTP_PASS"].replace(" ", "")   # Google shows the App Password with spaces
        from_addr = cfg["EMAIL_FROM"]
        # Gmail only sends as the authenticated account, so fall back to it if EMAIL_FROM is a placeholder.
        if "example" in from_addr or not parseaddr(from_addr)[1]:
            from_addr = formataddr(("GEOPOLITICS", user))
        msg = EmailMessage()
        msg["From"], msg["To"], msg["Subject"] = from_addr, job["to"], job["subject"]
        msg.set_content(job["text"])
        msg.add_alternative(job["html"], subtype="html")
        try:
            if cfg["SMTP_PORT"] == 465:
                server = smtplib.SMTP_SSL(cfg["SMTP_HOST"], 465, timeout=15)
            else:
                server = smtplib.SMTP(cfg["SMTP_HOST"], cfg["SMTP_PORT"], timeout=15)
                server.starttls()
            with server:
                server.login(user, pw)
                server.send_message(msg)
        except smtplib.SMTPAuthenticationError as exc:
            raise PermanentError("SMTP login rejected - use a Google App Password (needs 2-Step Verification), not your normal password") from exc
        except smtplib.SMTPRecipientsRefused as exc:
            raise PermanentError("recipient refused") from exc
        return "smtp"

    def _send(self, job):
        if self._provider() == "smtp":
            return self._send_smtp(job)
        r = requests.post(RESEND_URL, headers={"Authorization": f"Bearer {self.app.config['RESEND_API_KEY']}"},
                          json={"from": self.app.config["EMAIL_FROM"], "to": [job["to"]], "subject": job["subject"],
                                "html": job["html"], "text": job["text"]}, timeout=10)
        if r.status_code in (429,) or r.status_code >= 500:
            raise TemporaryError(f"provider returned {r.status_code}")
        if r.status_code >= 400:
            raise PermanentError(f"provider rejected the message ({r.status_code})")
        return r.json().get("id")
