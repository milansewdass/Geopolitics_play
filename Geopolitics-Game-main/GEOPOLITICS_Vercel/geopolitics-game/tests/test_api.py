"""API, authentication, authorisation, spectator, idempotency, persistence, commentary and admin tests."""
import random
import re

import pytest

from app import create_app
from app.engine import GameEngine

CFG = {"TESTING": True, "SECRET_KEY": "t", "MONGO_URI": "", "COMMENTARY_SYNC": True, "EMAIL_SYNC": True,
       "RATE_LIMIT_ENABLED": False, "ANTHROPIC_API_KEY": "", "RESEND_API_KEY": ""}


def make_app(seed=0, **extra):
    return create_app({**CFG, **extra}, engine=GameEngine(random.Random(seed)))


@pytest.fixture()
def app():
    return make_app()


def token(client, path="/login"):
    return re.search(r'name="csrf-token" content="([^"]+)"', client.get(path).get_data(as_text=True)).group(1)


def register(client, name="milan", email="milan@example.com", pw="Passw0rdX"):
    t = token(client, "/register")
    return client.post("/register", data={"username": name, "email": email, "password": pw, "confirm": pw, "csrf_token": t})


def call(client, method, url, body=None):
    t = token(client, "/")
    return getattr(client, method)(url, json=body if body is not None else {}, headers={"X-CSRF-Token": t})


def new_game(app, client, nation="ZA"):
    """Create a game for a logged-in client, searching engine seeds until the human can act."""
    for seed in range(60):
        app.engine = GameEngine(random.Random(seed))
        r = call(client, "post", "/api/games", {"nation": nation})
        assert r.status_code == 201
        st = r.get_json()["state"]
        if st["can_act"]:
            return st
        call(client, "delete", f"/api/games/{st['id']}")
    raise AssertionError("no seed gave a playable game")


# ---------------------------------------------------------------- accounts ----
def test_registration_validation_duplicates_and_verification_email(app):
    c = app.test_client()
    t = token(c, "/register")
    bad = c.post("/register", data={"username": "a!", "email": "x", "password": "x", "confirm": "y", "csrf_token": t})
    assert bad.status_code == 400
    assert register(c).status_code == 302
    assert register(app.test_client()).status_code == 400                    # same username/e-mail
    jobs = list(app.db.emails.col.find({"template": "verify"}))
    assert jobs and jobs[0]["status"] == "skipped"                          # recorded, no provider configured
    assert "milan@example.com" in jobs[0]["to"]


def test_csrf_login_required_and_lockout(app):
    c = app.test_client()
    assert c.get("/api/games").status_code == 401
    register(c)
    assert c.post("/api/games", json={"nation": "ZA"}).status_code == 400    # no CSRF token
    anon = app.test_client(); t = token(anon)
    for _ in range(5):
        assert anon.post("/login", data={"username": "milan", "password": "nope", "csrf_token": t}).status_code == 401
    assert anon.post("/login", data={"username": "milan", "password": "Passw0rdX", "csrf_token": t}).status_code == 429


def test_rate_limit_on_registration():
    app = make_app(RATE_LIMIT_ENABLED=True)
    from app.auth import _hits
    _hits.clear()
    c = app.test_client(); t = token(c, "/register"); codes = []
    for i in range(12):
        codes.append(c.post("/register", data={"username": "u", "email": "bad", "password": "x", "confirm": "x", "csrf_token": t}).status_code)
    assert 429 in codes
    _hits.clear()


# --------------------------------------------------------------- game + auth ----
def test_game_actions_versioning_and_idempotent_requests(app):
    c = app.test_client(); register(c)
    st = new_game(app, c); gid, v = st["id"], st["version"]
    r = call(c, "post", f"/api/games/{gid}/skip", {"version": v})
    assert r.status_code == 200
    after = r.get_json()["state"]
    assert after["version"] > v
    stale = call(c, "post", f"/api/games/{gid}/skip", {"version": v})        # network retry / double click
    assert stale.status_code == 409
    now = c.get(f"/api/games/{gid}").get_json()["state"]
    assert now["version"] == after["version"] and now["event_seq"] == after["event_seq"]   # nothing applied twice
    assert call(c, "post", f"/api/games/{gid}/skip", {}).status_code == 400  # missing version


def test_full_attack_flow_persists_events_and_battles(app):
    c = app.test_client(); register(c)
    st = new_game(app, c); gid = st["id"]
    game = app.db.games.get(gid)
    game.territories["ZA"].soldiers = 40
    foe = min(game.enemies_of(game.human_id), key=lambda t: t.soldiers)
    app.db.games.save(game)
    v = c.get(f"/api/games/{gid}").get_json()["state"]["version"]
    r = call(c, "post", f"/api/games/{gid}/attack/start", {"version": v, "from": "ZA", "to": foe.code})
    assert r.status_code == 200 and r.get_json()["state"]["battle"]
    v = r.get_json()["state"]["version"]
    r = call(c, "post", f"/api/games/{gid}/attack/roll", {"version": v, "blitz": True})
    body = r.get_json()
    assert r.status_code == 200 and body["result"]["outcome"] in ("conquest", "exhausted")
    assert any(e["type"] == "phase" for e in body["events"])
    # persisted history can be re-read after a "reload"
    ev = c.get(f"/api/games/{gid}/events?after=0").get_json()["events"]
    seqs = [e["seq"] for e in ev]
    assert seqs == sorted(set(seqs)) and seqs[0] == 1
    battles = c.get(f"/api/games/{gid}/battles").get_json()["battles"]
    assert battles and battles[0]["outcome"] in ("conquest", "exhausted")
    full = c.get(f"/api/games/{gid}/battles/{battles[0]['battle_no']}").get_json()["battle"]
    assert full["phases"] and all(len(p["attacker_dice"]) == len(p["defender_dice"]) for p in full["phases"])
    assert full["commentary"]["source"] == "fallback"                        # no AI key => clearly labelled fallback
    assert app.db.games.get(gid).version == c.get(f"/api/games/{gid}").get_json()["state"]["version"]


def test_owner_isolation_and_multiple_separate_games(app):
    a, b = app.test_client(), app.test_client()
    register(a); register(b, "viewer", "viewer@example.com")
    s1 = new_game(app, a); s2 = new_game(app, a, "BR")
    assert s1["id"] != s2["id"] and s1["share_code"] != s2["share_code"]
    assert len(a.get("/api/games").get_json()) == 2
    assert b.get(f"/api/games/{s1['id']}").status_code == 404
    assert call(b, "post", f"/api/games/{s1['id']}/skip", {"version": s1["version"]}).status_code == 404
    assert call(b, "delete", f"/api/games/{s1['id']}").status_code == 404
    assert b.get("/api/games").get_json() == []


def test_anonymous_spectator_is_read_only_and_public_safe(app):
    owner = app.test_client(); register(owner)
    st = new_game(app, owner)
    anon = app.test_client()
    page = anon.get(f"/watch/{st['share_code']}")
    assert page.status_code == 200
    r = anon.get(f"/api/watch/{st['share_code']}")
    pub = r.get_json()["state"]
    assert r.status_code == 200 and pub["can_act"] is False and pub["id"] is None and pub["pending"] is None
    blob = r.get_data(as_text=True)
    for secret in ("milan", "example.com", "owner_id", "password", st["id"]):
        assert secret not in blob
    assert anon.get(f"/api/watch/{st['share_code']}/events").status_code == 200
    assert anon.get(f"/api/watch/{st['share_code']}/battles").status_code == 200
    # every write is refused for a signed-out visitor, with or without CSRF
    t = token(anon, f"/watch/{st['share_code']}")
    for url in ("skip", "end-turn", "reinforce", "attack/start", "transfer", "fast-forward"):
        assert anon.post(f"/api/games/{st['id']}/{url}", json={"version": st["version"]}, headers={"X-CSRF-Token": t}).status_code == 401
    assert anon.get(f"/game/{st['id']}").status_code == 302                 # redirected to log in
    assert anon.get("/api/watch/nope").status_code == 404


def test_wrong_turn_and_illegal_actions_are_rejected_server_side(app):
    c = app.test_client(); register(c)
    st = new_game(app, c); gid = st["id"]
    foe = next(t for t in st["territories"] if t["owner"] != st["human_id"])
    v = st["version"]
    assert call(c, "post", f"/api/games/{gid}/attack/start", {"version": v, "from": foe["code"], "to": "ZA"}).status_code == 400
    assert call(c, "post", f"/api/games/{gid}/attack/roll", {"version": v}).status_code == 400
    assert call(c, "post", f"/api/games/{gid}/transfer", {"version": v, "from": "ZA", "to": foe["code"], "count": 1}).status_code == 400
    assert call(c, "post", f"/api/games/{gid}/fast-forward", {"version": v}).status_code == 400
    assert call(c, "post", f"/api/games/{gid}/reinforce", {"version": v, "country": "ZA", "count": "5"}).status_code == 400


def test_unsubscribe_and_notification_preferences(app):
    c = app.test_client(); register(c)
    d = c.get("/api/notifications").get_json()
    assert d["preferences"]["game_finished"] is True and d["preferences"]["new_game"] is False
    r = call(c, "put", "/api/notifications", {"new_game": True, "game_finished": False, "bogus": True})
    assert r.get_json()["preferences"] == {"new_game": True, "game_finished": False, "security_alerts": True}
    user = app.db.users.by_username("milan")
    tok = app.emailer.unsubscribe_token(str(user["_id"]), "new_game")
    assert app.test_client().get(f"/unsubscribe/{tok}").status_code == 200
    assert c.get("/api/notifications").get_json()["preferences"]["new_game"] is False
    assert app.test_client().get("/unsubscribe/garbage").status_code == 400


# -------------------------------------------------------------- commentary ----
def battle_rec():
    return {"battle_no": 1, "round": 1, "attacker": "A", "defender": "B", "from": "A", "to": "B", "att_start": 5,
            "def_start": 2, "att_lost": 1, "def_lost": 2, "outcome": "conquest", "gain": 2,
            "phases": [{"no": 1, "attacker_dice": [4, 5], "defender_dice": [1, 6], "att_loss": 1, "def_loss": 1,
                        "att_after": 4, "def_after": 1}]}


def test_commentary_fallback_ai_success_and_failure_never_leaks_key(monkeypatch):
    from app import commentary
    svc = commentary.CommentaryService("")
    out = svc.narrate(battle_rec())
    assert out["source"] == "fallback" and "B" in out["text"]

    class Resp:
        status_code = 200
        def raise_for_status(self): pass
        def json(self): return {"content": [{"type": "text", "text": "A storms B."}]}
    seen = {}
    def ok(url, headers, json, timeout):
        seen["prompt"] = json["messages"][0]["content"]; seen["headers"] = headers; return Resp()
    monkeypatch.setattr(commentary.requests, "post", ok)
    out = commentary.CommentaryService("sk-secret").narrate(battle_rec())
    assert out == {"text": "A storms B.", "source": "ai"}
    assert "4 v 1" in seen["prompt"] and "never invent" in seen["prompt"].lower()   # grounded in recorded facts

    def boom(*a, **k): raise RuntimeError("network down sk-secret")
    monkeypatch.setattr(commentary.requests, "post", boom)
    out = commentary.CommentaryService("sk-secret").narrate(battle_rec())
    assert out["source"] == "fallback" and "sk-secret" not in out["text"]


def test_gemini_provider_used_for_commentary(monkeypatch):
    from app import commentary
    seen = {}

    class Resp:
        status_code = 200
        def raise_for_status(self): pass
        def json(self): return {"candidates": [{"content": {"parts": [{"text": "Dice thunder."}]}}]}

    def ok(url, params=None, json=None, timeout=None, headers=None):
        seen["url"], seen["params"], seen["body"] = url, params, json
        return Resp()

    monkeypatch.setattr(commentary.requests, "post", ok)
    out = commentary.CommentaryService("", gemini_key="AIza-test").narrate(battle_rec())
    assert out == {"text": "Dice thunder.", "source": "ai"}
    assert "generativelanguage.googleapis.com" in seen["url"] and seen["params"]["key"] == "AIza-test"
    prompt = seen["body"]["contents"][0]["parts"][0]["text"]
    assert "never invent" in prompt.lower() and "4 v 1" in prompt      # grounded in recorded facts
    # Anthropic takes precedence when both keys are configured: a gemini-shaped response makes it fall back
    out2 = commentary.CommentaryService("sk-secret", gemini_key="AIza-test").narrate(battle_rec())
    assert out2["source"] == "fallback"


def test_commentary_cannot_change_game_state(app, monkeypatch):
    from app import commentary
    monkeypatch.setattr(commentary.requests, "post", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    app.commentary.api_key = "sk-x"
    c = app.test_client(); register(c)
    st = new_game(app, c); gid = st["id"]
    game = app.db.games.get(gid); game.territories["ZA"].soldiers = 30; app.db.games.save(game)
    foe = min(game.enemies_of(game.human_id), key=lambda t: t.soldiers)
    v = c.get(f"/api/games/{gid}").get_json()["state"]["version"]
    call(c, "post", f"/api/games/{gid}/attack/start", {"version": v, "from": "ZA", "to": foe.code})
    v = c.get(f"/api/games/{gid}").get_json()["state"]["version"]
    call(c, "post", f"/api/games/{gid}/attack/roll", {"version": v, "blitz": True})
    state = c.get(f"/api/games/{gid}").get_json()["state"]
    assert state["commentary"] and state["commentary"][-1]["source"] == "fallback"
    assert {t["code"]: (t["owner"], t["soldiers"]) for t in state["territories"]} == \
        {t.code: (t.owner, t.soldiers) for t in app.db.games.get(gid).territories.values()}


# ------------------------------------------------------------------- admin ----
def test_admin_role_is_server_assigned_only(app):
    c = app.test_client()
    register(c, "owner", "milan.sewdass@gmail.com")                          # the intended admin e-mail signs up like anyone
    assert c.get("/api/admin/overview").status_code == 403                  # signing up grants nothing
    assert app.db.users.by_username("owner").get("role") == "player"
    runner = app.test_cli_runner()
    r = runner.invoke(args=["grant-admin", "milan.sewdass@gmail.com"])
    assert r.exit_code != 0 and "not verified" in r.output                  # unverified e-mail cannot be promoted
    app.db.users.set(app.db.users.by_username("owner")["_id"], email_verified=True)
    assert runner.invoke(args=["grant-admin", "milan.sewdass@gmail.com"]).exit_code == 0
    assert c.get("/api/admin/overview").status_code == 200
    assert c.get("/admin").status_code == 200
    for path in ("users", "games", "emails", "errors", "audit"):
        assert c.get(f"/api/admin/{path}").status_code == 200
    assert any(a["action"] == "admin.grant" for a in c.get("/api/admin/audit").get_json())


def test_non_admin_and_anonymous_cannot_use_admin(app):
    c = app.test_client(); register(c)
    assert c.get("/api/admin/users").status_code == 403 and c.get("/admin").status_code == 403
    anon = app.test_client()
    assert anon.get("/api/admin/users").status_code == 401
    t = token(anon)
    assert anon.post("/api/admin/emails/process", json={}, headers={"X-CSRF-Token": t}).status_code == 401
    # a user cannot promote themselves by sending a role field anywhere
    c2 = app.test_client()
    c2.post("/register", data={"username": "sneak", "email": "s@example.com", "password": "Passw0rdX", "confirm": "Passw0rdX",
                               "role": "admin", "csrf_token": token(c2, "/register")})
    assert app.db.users.by_username("sneak")["role"] == "player"


# ------------------------------------------------------------------ pages ----
def test_public_and_signed_in_pages_render(app):
    c = app.test_client()
    for p in ("/", "/manual", "/login", "/register", "/healthz"):
        assert c.get(p).status_code == 200, p
    assert "GEOPOLITICS" in c.get("/").get_data(as_text=True)
    page = c.get("/manual").get_data(as_text=True)
    assert "university" not in page.lower() and "exam" not in page.lower() and "rubric" not in page.lower()
    register(c)
    for p in ("/new", "/headquarters", "/notifications", "/war-room", "/intelligence"):
        assert c.get(p, follow_redirects=True).status_code == 200, p
    countries = c.get("/api/countries").get_json()
    assert countries["source"]["retrieved"] and len(countries["countries"]) > 100


# -------------------------------------------------------------- live commentary + seeding ----
def test_live_phase_commentary_is_grounded_and_survives_next_save(app, monkeypatch):
    from app import commentary
    seen = []

    class Resp:
        def raise_for_status(self): pass
        def json(self): return {"content": [{"type": "text", "text": "Dice clatter."}]}
    monkeypatch.setattr(commentary.requests, "post", lambda url, headers, json, timeout: seen.append(json) or Resp())
    app.commentary.api_key = "sk-x"
    c = app.test_client(); register(c)
    st = new_game(app, c); gid = st["id"]
    game = app.db.games.get(gid); game.territories["ZA"].soldiers = 40
    foe = min(game.enemies_of(game.human_id), key=lambda t: t.soldiers); foe.soldiers = 30
    app.db.games.save(game)
    v = c.get(f"/api/games/{gid}").get_json()["state"]["version"]
    call(c, "post", f"/api/games/{gid}/attack/start", {"version": v, "from": "ZA", "to": foe.code})
    v = c.get(f"/api/games/{gid}").get_json()["state"]["version"]
    r = call(c, "post", f"/api/games/{gid}/attack/roll", {"version": v, "blitz": False}).get_json()
    assert r["state"]["battle"]                                   # battle still running
    live = [x for x in c.get(f"/api/games/{gid}").get_json()["state"]["commentary"] if x.get("live")]
    assert live and live[-1]["phase"] == 1 and live[-1]["source"] == "ai"
    ph = r["state"]["battle"]["phases"][0]
    prompt = seen[-1]["messages"][0]["content"]
    assert f"{ph['attacker_dice'][0]} v {ph['defender_dice'][0]}" in prompt and "NOT ended" in prompt
    # a later save of an older in-memory copy must not wipe the background commentary
    v = c.get(f"/api/games/{gid}").get_json()["state"]["version"]
    call(c, "post", f"/api/games/{gid}/attack/roll", {"version": v, "blitz": False})
    assert len([x for x in c.get(f"/api/games/{gid}").get_json()["state"]["commentary"] if x.get("live")]) >= 2


def test_seed_accounts_cli_creates_verified_accounts_and_only_named_admin(app):
    runner = app.test_cli_runner()
    res = runner.invoke(args=["seed-accounts", "boss@example.com", "rev.one@uni.example", "--admin", "boss@example.com"])
    assert res.exit_code == 0, res.output
    boss, rev = app.db.users.by_email("boss@example.com"), app.db.users.by_email("rev.one@uni.example")
    assert boss["role"] == "admin" and rev["role"] == "player" and boss["email_verified"] and rev["email_verified"]
    assert rev["username"] == "rev_one"
    pw = [ln.split()[3] for ln in res.output.splitlines() if ln.startswith("rev.one@")][0]
    c = app.test_client()
    t = token(c)
    r = c.post("/login", data={"username": "rev.one@uni.example", "password": pw, "csrf_token": t})
    assert r.status_code == 302                                   # e-mail login works with the issued password
    again = runner.invoke(args=["seed-accounts", "rev.one@uni.example"])
    assert "already exists" in again.output and app.db.users.count() == 2


def test_seed_accounts_from_csv_uses_given_passwords(app, tmp_path):
    f = tmp_path / "c.csv"
    f.write_text("email,password\nA.Person@Uni.example,Abcdefgh1234\n")
    res = app.test_cli_runner().invoke(args=["seed-accounts", "--from-csv", str(f)])
    assert res.exit_code == 0, res.output
    c = app.test_client(); t = token(c)
    assert c.post("/login", data={"username": "a.person@uni.example", "password": "Abcdefgh1234", "csrf_token": t}).status_code == 302


def test_smtp_provider_used_when_configured(monkeypatch):
    import smtplib
    from app import create_app
    sent = []

    class FakeSMTP:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def starttls(self): pass
        def login(self, u, p): sent.append(("login", u))
        def send_message(self, m): sent.append(("msg", m["To"], m["From"]))

    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    app = create_app({"TESTING": True, "SECRET_KEY": "x", "RESEND_API_KEY": "", "SMTP_HOST": "smtp.gmail.com",
                      "SMTP_PORT": 587, "SMTP_USER": "me@gmail.com", "SMTP_PASS": "pw", "EMAIL_SYNC": True})
    job = {"to": "a@b.co", "subject": "s", "html": "<p>x</p>", "text": "x"}
    assert app.emailer._provider() == "smtp"
    assert app.emailer._send(job) == "smtp"
    assert ("msg", "a@b.co", "GEOPOLITICS <me@gmail.com>") in sent


def test_cron_process_emails_requires_cron_secret_and_processes_due(app):
    c = app.test_client()
    assert c.post("/api/cron/process-emails").status_code == 503    # CRON_SECRET not configured
    app.config["CRON_SECRET"] = "cron-s3cret"
    assert c.post("/api/cron/process-emails").status_code == 401    # no bearer token
    assert c.post("/api/cron/process-emails", headers={"Authorization": "Bearer wrong"}).status_code == 401
    job = app.db.emails.create({"template": "test", "to": "a@b.co", "user_id": None, "dedupe_key": "cron-test",
                                "subject": "s", "html": "<p>x</p>", "text": "x"})
    assert job and job["status"] == "queued"
    # the bearer-authenticated call needs no session/CSRF and picks up the due job
    r = c.post("/api/cron/process-emails", headers={"Authorization": "Bearer cron-s3cret"})
    assert r.status_code == 200 and r.get_json() == {"processed": 1}
    assert app.db.emails.get(str(job["_id"]))["status"] == "skipped"    # no provider configured -> recorded, not lost
