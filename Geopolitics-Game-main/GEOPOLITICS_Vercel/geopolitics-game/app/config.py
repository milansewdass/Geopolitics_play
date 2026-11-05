import os


def _flag(name, default="0"):
    return os.environ.get(name, default) == "1"


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY") or "dev-only-change-me"
    # --- database (MongoDB Atlas). Empty URI => in-memory mongomock for local dev/tests only.
    MONGO_URI = os.environ.get("MONGO_URI", "")
    MONGO_DB_NAME = os.environ.get("MONGO_DB_NAME", "geopolitics")
    # --- AI commentary (server side only). Anthropic is used when configured;
    # --- otherwise Gemini (AI Studio free tier). Without any key, labelled automated summaries are used.
    ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
    ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-haiku-5-5")
    GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
    GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash")
    COMMENTARY_SYNC = False            # tests set True; production narrates in a background thread
    # --- e-mail (Resend). Without a key e-mails are recorded as "skipped" and the app works normally.
    RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
    # --- e-mail via SMTP (e.g. Gmail with an App Password). Used when RESEND_API_KEY is empty.
    SMTP_HOST = os.environ.get("SMTP_HOST", "")
    SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
    SMTP_USER = os.environ.get("SMTP_USER", "")
    SMTP_PASS = os.environ.get("SMTP_PASS", "")
    EMAIL_FROM = os.environ.get("EMAIL_FROM", "GEOPOLITICS <no-reply@example.com>")
    PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "http://localhost:5000")
    EMAIL_SYNC = False
    # Shared secret for Vercel Cron Jobs (sent as a Bearer token). Without it the
    # /api/cron/process-emails endpoint refuses to run.
    CRON_SECRET = os.environ.get("CRON_SECRET", "")
    # Opt-in operational alerts to the owner (throttled + de-duplicated)
    ADMIN_ALERTS_ENABLED = _flag("ADMIN_ALERTS_ENABLED")
    ADMIN_ALERT_EMAIL = os.environ.get("ADMIN_ALERT_EMAIL", "milan.sewdass@gmail.com")
    # The intended administrator. This is an *identity hint only*: admin rights are granted by the
    # server-side command `flask grant-admin`, never from this value or from sign-up.
    ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "milan.sewdass@gmail.com")
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = _flag("COOKIE_SECURE")
    MAX_CONTENT_LENGTH = 64 * 1024
    RATE_LIMIT_ENABLED = True
    # Set automatically by Vercel (VERCEL=1). Turns off background threads; requires MONGO_URI.
    SERVERLESS = os.environ.get("VERCEL") == "1"
