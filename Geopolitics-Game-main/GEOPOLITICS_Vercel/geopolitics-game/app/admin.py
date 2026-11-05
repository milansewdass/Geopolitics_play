"""Administrator console (server-authorised). Every route requires a verified admin account whose
role lives in the database; every mutating action is written to the audit log."""
from __future__ import annotations

from datetime import timedelta

from flask import Blueprint, current_app, g, jsonify, render_template, request

from .auth import admin_required
from .db import to_oid
from .models import now

bp = Blueprint("admin", __name__)


def _audit(action, target=None, **extra):
    current_app.db.audit.add(actor=g.user["username"], action=action, target=target, **extra)


def _public_user(u):
    return {"id": str(u["_id"]), "username": u["username"], "email": u.get("email"),
            "role": u.get("role", "player"), "email_verified": bool(u.get("email_verified")),
            "disabled": bool(u.get("disabled")), "failed_logins": u.get("failed_logins", 0),
            "created_at": u["created_at"].isoformat() if u.get("created_at") else None}


@bp.route("/admin")
@admin_required
def console():
    return render_template("admin.html")


@bp.route("/api/admin/overview")
@admin_required
def overview():
    db = current_app.db
    day = now() - timedelta(hours=24)
    hour = now() - timedelta(hours=1)
    abuse = []
    for u in db.users.search(limit=200):
        recent = db.games.created_since(str(u["_id"]), hour)
        if recent >= 10 or u.get("failed_logins", 0) >= 3 or u.get("locked_until"):
            abuse.append({"username": u["username"], "games_last_hour": recent,
                          "failed_logins": u.get("failed_logins", 0), "locked": bool(u.get("locked_until"))})
    return jsonify({
        "users": db.users.count(), "admins": db.users.count({"role": "admin"}),
        "unverified": db.users.count({"email_verified": False}),
        "games_active": db.games.count({"status": "active"}),
        "games_completed": db.games.count({"status": "finished"}),
        "errors_24h": db.errors.count_since(day), "email": db.emails.counts(),
        "email_provider_configured": bool(current_app.config["RESEND_API_KEY"] or current_app.config["SMTP_HOST"]),
        "database": db.status(), "abuse_indicators": abuse,
    })


@bp.route("/api/admin/users")
@admin_required
def users():
    return jsonify([_public_user(u) for u in current_app.db.users.search(request.args.get("q", "")[:60])])


@bp.route("/api/admin/users/<uid>/disable", methods=["POST"])
@admin_required
def disable_user(uid):
    user = current_app.db.users.by_id(uid)
    if not user:
        return jsonify(error="User not found."), 404
    if user["_id"] == g.user["_id"]:
        return jsonify(error="You cannot disable your own account."), 400
    flag = bool((request.get_json(silent=True) or {}).get("disabled", True))
    current_app.db.users.set(user["_id"], disabled=flag)
    _audit("user.disable" if flag else "user.enable", user["username"])
    return jsonify(ok=True)


@bp.route("/api/admin/games")
@admin_required
def games():
    status = request.args.get("status")
    status = status if status in ("active", "finished") else None
    out = []
    for d in current_app.db.games.search(status):
        owner = current_app.db.users.by_id(d["owner_id"])
        out.append({"id": str(d["_id"]), "owner": owner["username"] if owner else "?", "status": d["status"],
                    "round": d["round"], "share_code": d["share_code"],
                    "human": next((p["name"] for p in d["players"] if p["is_human"]), ""),
                    "updated_at": d["updated_at"].isoformat()})
    return jsonify(out)


@bp.route("/api/admin/games/<gid>", methods=["DELETE"])
@admin_required
def delete_game(gid):
    oid = to_oid(gid)
    if not oid or not current_app.db.games.delete(oid):
        return jsonify(error="Game not found."), 404
    current_app.db.events.delete_for_game(oid)
    current_app.db.battles.delete_for_game(oid)
    _audit("game.delete", gid)
    return jsonify(ok=True)


@bp.route("/api/admin/emails")
@admin_required
def emails():
    rows = current_app.db.emails.recent(request.args.get("status") or None)
    return jsonify([{"id": str(r["_id"]), "template": r["template"], "to": r["to"], "status": r["status"],
                     "attempts": r["attempts"], "last_error": r.get("last_error"),
                     "created_at": r["created_at"].isoformat()} for r in rows])


@bp.route("/api/admin/emails/<jid>/retry", methods=["POST"])
@admin_required
def retry_email(jid):
    ok = current_app.emailer.requeue(jid)
    _audit("email.retry", jid)
    return jsonify(ok=ok)


@bp.route("/api/admin/emails/process", methods=["POST"])
@admin_required
def process_emails():
    n = current_app.emailer.process_due()
    _audit("email.process_due", extra_count=n)
    return jsonify(processed=n)


@bp.route("/api/admin/errors")
@admin_required
def errors():
    return jsonify([{**e, "created_at": e["created_at"].isoformat()} for e in current_app.db.errors.recent()])


@bp.route("/api/admin/audit")
@admin_required
def audit():
    return jsonify([{**e, "created_at": e["created_at"].isoformat()} for e in current_app.db.audit.recent()])
