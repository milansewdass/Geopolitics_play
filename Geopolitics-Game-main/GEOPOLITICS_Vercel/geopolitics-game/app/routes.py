"""HTML pages and the JSON API.

Authorisation model
  * anyone may WATCH a game through its public share code (read-only, public-safe projection);
  * only a signed-in account may create games, and only the owner may change a game;
  * every state-changing call carries the game `version` the client last saw, so refreshes, double
    clicks, second tabs and network retries cannot apply the same action twice.
"""
from __future__ import annotations

from flask import Blueprint, current_app, g, jsonify, redirect, render_template, request, url_for

from .auth import login_required, rate_limit
from .catalog import COUNTRIES, POPULATION_SOURCE
from .db import ConflictError
from .engine import GameError

pages = Blueprint("pages", __name__)
api = Blueprint("api", __name__, url_prefix="/api")


class NotFound(Exception):
    pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _uid():
    return str(g.user["_id"])


def _owned(game_id):
    game = current_app.db.games.get(game_id)
    if not game or game.owner_id != _uid():
        raise NotFound()
    return game


def _by_code(code):
    game = current_app.db.games.by_share_code(code)
    if not game:
        raise NotFound()
    return game


def _json():
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


def _int(value, name="count"):
    if isinstance(value, bool) or not isinstance(value, int):
        raise GameError(f"{name} must be a whole number.")
    return value


def _code(value, name):
    if not isinstance(value, str) or not (2 <= len(value) <= 3):
        raise GameError(f"{name} is required.")
    return value.upper()


def _action_game(game_id):
    """Load an owned game for a mutating call and enforce the optimistic version."""
    game = _owned(game_id)
    version = _json().get("version")
    if isinstance(version, bool) or not isinstance(version, int):
        raise GameError("Missing game version. Reload the page.")
    if version != game.version:
        raise ConflictError("The game has already moved on (duplicate or stale request). Reloaded the latest state.")
    return game


def _commit(game, result=None):
    """Persist the game (atomic, versioned), then its event/battle history; start commentary."""
    db = current_app.db
    events, game.new_events = game.new_events, []
    battles, game.new_battles = game.new_battles, []
    db.games.save(game)
    db.events.insert_many(game.id, events)
    db.battles.insert_many(game.id, battles)
    if battles:
        current_app.schedule_commentary(game.id, battles)
    if game.status == "finished":
        _queue_finished_email(game)
    return jsonify(state=game.view(True), events=events, result=result or {})


def _queue_finished_email(game):
    owner = current_app.db.users.by_id(game.owner_id)
    won = game.winner == game.human_id
    summary = (f"Victory! {game.human.name} now controls all {len(game.territories)} countries."
               if won else f"{game.players[game.winner].name} conquered the world in round {game.round}.")
    current_app.emailer.queue("game_finished", owner, f"finished:{game.id}",
                              {"summary": summary, "link": f"{_base()}/game/{game.id}"}, pref="game_finished")


def _base():
    return current_app.config["PUBLIC_BASE_URL"].rstrip("/")


def _events_payload(game):
    try:
        after = int(request.args.get("after", 0))
    except ValueError:
        after = 0
    events = current_app.db.events.after(game.id, after, request.args.get("limit", 300))
    return jsonify(events=events, last_seq=game.event_seq)


def _battle_payload(game, no):
    rec = current_app.db.battles.get(game.id, no)
    if not rec:
        raise NotFound()
    rec["created_at"] = rec["created_at"].isoformat() if rec.get("created_at") else None
    return jsonify(battle=rec, events=current_app.db.events.for_battle(game.id, no))


def _battle_list(game):
    rows = current_app.db.battles.list(game.id)
    for r in rows:
        r["created_at"] = r["created_at"].isoformat() if r.get("created_at") else None
    return jsonify(battles=rows)


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------
@pages.route("/")
def home():
    return render_template("home.html")


@pages.route("/manual")
def manual():
    return render_template("manual.html", source=POPULATION_SOURCE)


@pages.route("/new")
@login_required
def new_game_page():
    return render_template("new.html")


@pages.route("/headquarters")
@login_required
def headquarters():
    return render_template("headquarters.html")


@pages.route("/dashboard")
def dashboard_redirect():
    return redirect(url_for("pages.headquarters"))


@pages.route("/war-room")
@login_required
def war_room_latest():
    games = current_app.db.games.list_for_owner(_uid())
    active = next((x for x in games if x.status == "active"), games[0] if games else None)
    return redirect(url_for("pages.game_page", game_id=active.id) if active else url_for("pages.headquarters"))


@pages.route("/intelligence")
@login_required
def intelligence_latest():
    games = current_app.db.games.list_for_owner(_uid())
    active = next((x for x in games if x.status == "active"), games[0] if games else None)
    return redirect(url_for("pages.intelligence_page", game_id=active.id) if active else url_for("pages.headquarters"))


@pages.route("/game/<game_id>")
@login_required
def game_page(game_id):
    game = _owned(game_id)
    return render_template("game.html", mode="play", ref=str(game.id), code=game.share_code)


@pages.route("/intelligence/<game_id>")
@login_required
def intelligence_page(game_id):
    game = _owned(game_id)
    return render_template("intelligence.html", mode="play", ref=str(game.id), code=game.share_code)


@pages.route("/watch/<code>")
def watch_page(code):
    _by_code(code)
    return render_template("game.html", mode="watch", ref=code, code=code)


@pages.route("/watch/<code>/intelligence")
def watch_intelligence(code):
    _by_code(code)
    return render_template("intelligence.html", mode="watch", ref=code, code=code)


@pages.route("/spectate/<code>")
def spectate_legacy(code):
    return redirect(url_for("pages.watch_page", code=code))


@pages.route("/notifications")
@login_required
def notifications_page():
    return render_template("notifications.html")


@pages.route("/unsubscribe/<token>")
def unsubscribe(token):
    data = current_app.emailer.read_unsubscribe_token(token)
    if not data or data.get("pref") not in current_app.db.prefs.DEFAULTS:
        return render_template("error.html", message="This unsubscribe link is invalid."), 400
    current_app.db.prefs.set(data["uid"], {data["pref"]: False})
    return render_template("error.html", title="Unsubscribed", message="You will no longer receive that kind of e-mail.")


@pages.app_errorhandler(NotFound)
def _not_found(_):
    if request.path.startswith("/api/"):
        return jsonify(error="Not found."), 404
    return render_template("error.html", message="That page or game could not be found."), 404


@pages.app_errorhandler(GameError)
def _game_error(e):
    return jsonify(error=str(e)), 400


@pages.app_errorhandler(ConflictError)
def _conflict(e):
    return jsonify(error=str(e)), 409


# ---------------------------------------------------------------------------
# Reference data (public)
# ---------------------------------------------------------------------------
@api.route("/countries")
def countries():
    return jsonify(source=POPULATION_SOURCE, countries=COUNTRIES)


# ---------------------------------------------------------------------------
# Campaigns (owner)
# ---------------------------------------------------------------------------
@api.route("/games", methods=["GET"])
@login_required
def list_games():
    out = []
    for game in current_app.db.games.list_for_owner(_uid()):
        me = game.human
        out.append({
            "id": str(game.id), "nation": me.name, "status": game.status, "round": game.round,
            "controlled": len(game.owned(game.human_id)),
            "soldiers": sum(t.soldiers for t in game.owned(game.human_id)),
            "nations": len(game.territories), "adversaries": len(game.alive_players()) - (0 if me.eliminated else 1),
            "eliminated": me.eliminated, "won": game.winner == game.human_id, "share_code": game.share_code,
            "updated_at": game.updated_at.isoformat() if game.updated_at else None,
        })
    return jsonify(out)


@api.route("/games", methods=["POST"])
@login_required
@rate_limit("newgame", 20, 3600)
def create_game():
    nation = _code(_json().get("nation"), "nation")
    game = current_app.engine.new_game(_uid(), nation)
    current_app.db.games.insert(game)
    events, game.new_events = game.new_events, []
    battles, game.new_battles = game.new_battles, []
    current_app.db.events.insert_many(game.id, events)
    current_app.db.battles.insert_many(game.id, battles)
    if battles:
        current_app.schedule_commentary(game.id, battles)
    current_app.emailer.queue("new_game", g.user, f"newgame:{game.id}",
                              {"nation": game.human.name, "link": f"{_base()}/game/{game.id}"}, pref="new_game")
    return jsonify(state=game.view(True), events=events), 201


@api.route("/games/<game_id>", methods=["GET"])
@login_required
def get_game(game_id):
    return jsonify(state=_owned(game_id).view(True))


@api.route("/games/<game_id>", methods=["DELETE"])
@login_required
def delete_game(game_id):
    game = _owned(game_id)
    current_app.db.games.delete(game.id, _uid())
    current_app.db.events.delete_for_game(game.id)
    current_app.db.battles.delete_for_game(game.id)
    return jsonify(ok=True)


@api.route("/games/<game_id>/events")
@login_required
def game_events(game_id):
    return _events_payload(_owned(game_id))


@api.route("/games/<game_id>/battles")
@login_required
def game_battles(game_id):
    return _battle_list(_owned(game_id))


@api.route("/games/<game_id>/battles/<int:no>")
@login_required
def game_battle(game_id, no):
    return _battle_payload(_owned(game_id), no)


# ---------------------------------------------------------------------------
# Public watch (no account needed, strictly read-only)
# ---------------------------------------------------------------------------
@api.route("/watch/<code>")
def watch(code):
    return jsonify(state=_by_code(code).view(False))     # public-safe projection, never any controls


@api.route("/watch/<code>/events")
def watch_events(code):
    return _events_payload(_by_code(code))


@api.route("/watch/<code>/battles")
def watch_battles(code):
    return _battle_list(_by_code(code))


@api.route("/watch/<code>/battles/<int:no>")
def watch_battle(code, no):
    return _battle_payload(_by_code(code), no)


# ---------------------------------------------------------------------------
# Turn actions (owner only; the engine enforces the rules)
# ---------------------------------------------------------------------------
@api.route("/games/<game_id>/reinforce", methods=["POST"])
@login_required
def reinforce(game_id):
    game, d = _action_game(game_id), _json()
    current_app.engine.reinforce(game, game.human_id, _code(d.get("country"), "country"), _int(d.get("count", 1)))
    return _commit(game)


@api.route("/games/<game_id>/attack/start", methods=["POST"])
@login_required
def attack_start(game_id):
    game, d = _action_game(game_id), _json()
    current_app.engine.start_attack(game, game.human_id, _code(d.get("from"), "from"), _code(d.get("to"), "to"))
    return _commit(game)


@api.route("/games/<game_id>/attack/roll", methods=["POST"])
@login_required
def attack_roll(game_id):
    game, d = _action_game(game_id), _json()
    if d.get("blitz"):
        phases, outcome = current_app.engine.blitz(game, game.human_id)
    else:
        phase, outcome = current_app.engine.roll_phase(game, game.human_id)
        phases = [phase]
    live = _live_phase_context(game)
    resp = _commit(game, {"outcome": outcome})
    if live:
        current_app.schedule_phase_commentary(game.id, live)
    return resp


def _live_phase_context(game):
    """Verified facts of the latest phase while the battle is still running (None once it has ended;
    finished battles get full-battle commentary instead)."""
    b = game.battle
    if not b or not b["phases"]:
        return None
    return {"battle_no": b["no"], "round": game.round, "attacker": game.players[b["attacker"]].name,
            "defender": game.players[b["defender"]].name, "from": game.territories[b["from"]].name,
            "to": game.territories[b["to"]].name, "att_start": b["att_start"], "def_start": b["def_start"],
            "phase": dict(b["phases"][-1])}


@api.route("/games/<game_id>/attack/stop", methods=["POST"])
@login_required
def attack_stop(game_id):
    game = _action_game(game_id)
    current_app.engine.stop_attack(game, game.human_id)
    return _commit(game, {"outcome": "withdrawn"})


@api.route("/games/<game_id>/transfer", methods=["POST"])
@login_required
def transfer(game_id):
    game, d = _action_game(game_id), _json()
    current_app.engine.transfer(game, game.human_id, _code(d.get("from"), "from"), _code(d.get("to"), "to"),
                                _int(d.get("count")))
    return _commit(game)


@api.route("/games/<game_id>/skip", methods=["POST"])
@login_required
def skip(game_id):
    game = _action_game(game_id)
    current_app.engine.skip_turn(game, game.human_id)
    return _commit(game)


@api.route("/games/<game_id>/end-turn", methods=["POST"])
@login_required
def end_turn(game_id):
    game = _action_game(game_id)
    current_app.engine.end_turn(game, game.human_id)
    return _commit(game)


@api.route("/games/<game_id>/fast-forward", methods=["POST"])
@login_required
def fast_forward(game_id):
    game = _action_game(game_id)
    played = current_app.engine.fast_forward(game)
    return _commit(game, {"turns_played": played})


# ---------------------------------------------------------------------------
# Analytics, notification preferences
# ---------------------------------------------------------------------------
@api.route("/stats")
@login_required
def stats():
    totals = {"games": 0, "completed": 0, "wins": 0, "battles_won": 0, "battles_lost": 0,
              "territories_captured": 0, "soldiers_lost": 0, "soldiers_killed": 0}
    for game in current_app.db.games.list_for_owner(_uid()):
        me = game.human
        totals["games"] += 1
        if game.status == "finished":
            totals["completed"] += 1
            totals["wins"] += 1 if game.winner == game.human_id else 0
        for k in ("battles_won", "battles_lost", "territories_captured", "soldiers_lost", "soldiers_killed"):
            totals[k] += me.stats.get(k, 0)
    return jsonify(totals)


@api.route("/notifications", methods=["GET", "PUT"])
@login_required
def notifications():
    db = current_app.db
    if request.method == "PUT":
        body = _json()
        db.prefs.set(_uid(), {k: v for k, v in body.items() if isinstance(v, bool)})
    jobs = db.emails.for_user(_uid())
    return jsonify(preferences=db.prefs.get(_uid()), email=g.user.get("email"),
                   email_verified=bool(g.user.get("email_verified")),
                   deliveries=[{"template": j["template"], "status": j["status"], "attempts": j["attempts"],
                                "created_at": j["created_at"].isoformat()} for j in jobs])
