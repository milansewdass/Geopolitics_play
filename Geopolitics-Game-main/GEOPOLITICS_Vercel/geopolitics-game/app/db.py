"""Database connection and repositories (the data-access layer).

Collections
  users                  account, e-mail, role, lock-out counters
  games                  one document per campaign (authoritative state, versioned)
  events                 append-only history of every event of every game  (game_id, seq) unique
  battles                one document per finished battle incl. every dice phase + commentary
  notification_prefs     per-user e-mail preferences
  email_jobs             outbound e-mail queue / delivery log (dedupe key unique)
  tokens                 e-mail verification + password-reset tokens (hashed)
  audit_log              administrator actions
  error_log              server-side errors shown in the admin console
"""
from __future__ import annotations

import hashlib
from datetime import timedelta

from bson import ObjectId
from bson.errors import InvalidId
from pymongo import ASCENDING, DESCENDING, MongoClient
from pymongo.errors import BulkWriteError, DuplicateKeyError

from .models import Game, now


class ConflictError(Exception):
    pass


def to_oid(value):
    try:
        return ObjectId(str(value))
    except (InvalidId, TypeError):
        return None


class Database:
    """Owns the Mongo connection. Falls back to an in-memory mongomock for dev/tests."""

    def __init__(self, uri: str, name: str):
        self.memory = not uri
        if uri:
            self.client = MongoClient(uri, serverSelectionTimeoutMS=8000)
            self.client.admin.command("ping")  # fail fast with a clear error
        else:
            import mongomock
            self.client = mongomock.MongoClient()
        self.db = self.client[name]
        self.users = UserRepository(self.db.users)
        self.games = GameRepository(self.db.games)
        self.events = EventRepository(self.db.events)
        self.battles = BattleRepository(self.db.battles)
        self.prefs = PrefsRepository(self.db.notification_prefs)
        self.emails = EmailJobRepository(self.db.email_jobs)
        self.tokens = TokenRepository(self.db.tokens)
        self.audit = LogRepository(self.db.audit_log)
        self.errors = LogRepository(self.db.error_log)

    def status(self):
        """Live connection evidence for the admin console / health checks."""
        info = {"mode": "in-memory (mongomock)" if self.memory else "MongoDB (online)",
                "database": self.db.name}
        if not self.memory:
            info["server_version"] = self.client.server_info().get("version")
        info["collections"] = sorted(self.db.list_collection_names())
        return info


class UserRepository:
    def __init__(self, col):
        self.col = col
        col.create_index("username", unique=True)
        col.create_index("email", unique=True, sparse=True)

    def create(self, username, email, password_hash, role="player"):
        doc = {"username": username, "email": email, "email_verified": False, "role": role,
               "password_hash": password_hash, "created_at": now(), "failed_logins": 0,
               "locked_until": None, "disabled": False}
        doc["_id"] = self.col.insert_one(doc).inserted_id
        return doc

    def by_username(self, username):
        return self.col.find_one({"username": username})

    def by_email(self, email):
        return self.col.find_one({"email": email}) if email else None

    def by_id(self, uid):
        oid = to_oid(uid)
        return self.col.find_one({"_id": oid}) if oid else None

    def record_failure(self, uid, lock_after, lock_until):
        doc = self.col.find_one_and_update({"_id": uid}, {"$inc": {"failed_logins": 1}}, return_document=True)
        if doc and doc["failed_logins"] >= lock_after:
            self.col.update_one({"_id": uid}, {"$set": {"locked_until": lock_until, "failed_logins": 0}})

    def clear_failures(self, uid):
        self.col.update_one({"_id": uid}, {"$set": {"failed_logins": 0, "locked_until": None}})

    def set(self, uid, **fields):
        self.col.update_one({"_id": uid}, {"$set": fields})

    def search(self, q="", limit=50):
        flt = {}
        if q:
            import re
            rx = {"$regex": re.escape(q), "$options": "i"}
            flt = {"$or": [{"username": rx}, {"email": rx}]}
        return list(self.col.find(flt, {"password_hash": 0}).sort("created_at", DESCENDING).limit(limit))

    def count(self, flt=None):
        return self.col.count_documents(flt or {})


class GameRepository:
    def __init__(self, col):
        self.col = col
        col.create_index([("owner_id", ASCENDING), ("updated_at", DESCENDING)])
        col.create_index("share_code", unique=True)
        col.create_index([("status", ASCENDING), ("updated_at", DESCENDING)])

    def insert(self, game: Game):
        game.id = self.col.insert_one(game.to_doc()).inserted_id
        return game

    def get(self, game_id):
        oid = to_oid(game_id)
        doc = self.col.find_one({"_id": oid}) if oid else None
        return Game.from_doc(doc) if doc else None

    def by_share_code(self, code):
        doc = self.col.find_one({"share_code": str(code)})
        return Game.from_doc(doc) if doc else None

    def list_for_owner(self, owner_id):
        cur = self.col.find({"owner_id": owner_id}).sort("updated_at", DESCENDING)
        return [Game.from_doc(d) for d in cur]

    def save(self, game: Game):
        """Atomic compare-and-swap on the version: a stale or duplicated request cannot overwrite
        (or re-apply on top of) newer state."""
        expected = game.version
        game.version += 1
        game.updated_at = now()
        doc = game.to_doc()
        doc.pop("_id", None)
        # `commentary` is appended by background narration ($push); never overwrite it from a
        # possibly older in-memory copy, otherwise a fast next click could erase fresh commentary.
        doc.pop("commentary", None)
        res = self.col.update_one({"_id": game.id, "version": expected}, {"$set": doc})
        if res.matched_count != 1:
            game.version = expected
            raise ConflictError("The game changed in another window or request. Reload and try again.")

    def delete(self, game_id, owner_id=None):
        oid = to_oid(game_id)
        flt = {"_id": oid} if owner_id is None else {"_id": oid, "owner_id": owner_id}
        return bool(oid) and self.col.delete_one(flt).deleted_count == 1

    def count(self, flt=None):
        return self.col.count_documents(flt or {})

    def search(self, status=None, limit=50):
        flt = {"status": status} if status else {}
        return list(self.col.find(flt, {"players": 1, "status": 1, "round": 1, "owner_id": 1,
                                        "created_at": 1, "updated_at": 1, "winner": 1,
                                        "share_code": 1}).sort("updated_at", DESCENDING).limit(limit))

    def created_since(self, owner_id, since):
        return self.col.count_documents({"owner_id": owner_id, "created_at": {"$gte": since}})


class EventRepository:
    def __init__(self, col):
        self.col = col
        col.create_index([("game_id", ASCENDING), ("seq", ASCENDING)], unique=True)
        col.create_index([("game_id", ASCENDING), ("data.battle_no", ASCENDING)])

    def insert_many(self, game_id, events):
        if not events:
            return
        docs = [{**e, "game_id": game_id} for e in events]
        try:
            self.col.insert_many(docs, ordered=False)
        except (BulkWriteError, DuplicateKeyError):
            pass  # already stored (retry): the unique (game_id, seq) index makes this idempotent

    def after(self, game_id, seq=0, limit=300):
        cur = self.col.find({"game_id": game_id, "seq": {"$gt": int(seq)}}, {"_id": 0, "game_id": 0}) \
            .sort("seq", ASCENDING).limit(min(int(limit), 1000))
        return list(cur)

    def for_battle(self, game_id, battle_no):
        cur = self.col.find({"game_id": game_id, "data.battle_no": int(battle_no)}, {"_id": 0, "game_id": 0}) \
            .sort("seq", ASCENDING)
        return list(cur)

    def delete_for_game(self, game_id):
        self.col.delete_many({"game_id": game_id})


class BattleRepository:
    def __init__(self, col):
        self.col = col
        col.create_index([("game_id", ASCENDING), ("battle_no", ASCENDING)], unique=True)

    def insert_many(self, game_id, records):
        if not records:
            return
        docs = [{**r, "game_id": game_id, "created_at": now(), "commentary": None} for r in records]
        try:
            self.col.insert_many(docs, ordered=False)
        except (BulkWriteError, DuplicateKeyError):
            pass

    def list(self, game_id, limit=100):
        return list(self.col.find({"game_id": game_id}, {"_id": 0, "game_id": 0, "phases": 0})
                    .sort("battle_no", DESCENDING).limit(limit))

    def get(self, game_id, battle_no):
        return self.col.find_one({"game_id": game_id, "battle_no": int(battle_no)}, {"_id": 0, "game_id": 0})

    def set_commentary(self, game_id, battle_no, commentary):
        self.col.update_one({"game_id": game_id, "battle_no": battle_no}, {"$set": {"commentary": commentary}})

    def delete_for_game(self, game_id):
        self.col.delete_many({"game_id": game_id})


class PrefsRepository:
    DEFAULTS = {"new_game": False, "game_finished": True, "security_alerts": True}

    def __init__(self, col):
        self.col = col
        col.create_index("user_id", unique=True)

    def get(self, user_id):
        doc = self.col.find_one({"user_id": str(user_id)}) or {}
        return {k: bool(doc.get(k, v)) for k, v in self.DEFAULTS.items()}

    def set(self, user_id, prefs):
        clean = {k: bool(prefs[k]) for k in self.DEFAULTS if k in prefs}
        self.col.update_one({"user_id": str(user_id)}, {"$set": {**clean, "updated_at": now()}}, upsert=True)
        return self.get(user_id)


class EmailJobRepository:
    MAX_ATTEMPTS = 5

    def __init__(self, col):
        self.col = col
        col.create_index("dedupe_key", unique=True)
        col.create_index([("status", ASCENDING), ("next_attempt_at", ASCENDING)])
        col.create_index([("user_id", ASCENDING), ("created_at", DESCENDING)])

    def create(self, job):
        """Returns the new job, or None when a job with the same dedupe key already exists."""
        doc = {"status": "queued", "attempts": 0, "last_error": None, "created_at": now(),
               "next_attempt_at": now(), "provider_id": None, **job}
        try:
            doc["_id"] = self.col.insert_one(doc).inserted_id
        except DuplicateKeyError:
            return None
        return doc

    def get(self, jid):
        return self.col.find_one({"_id": to_oid(jid)})

    def mark(self, jid, **fields):
        self.col.update_one({"_id": jid}, {"$set": fields})

    def attempt_failed(self, job, error):
        attempts = job["attempts"] + 1
        if attempts >= self.MAX_ATTEMPTS:
            self.mark(job["_id"], status="failed", attempts=attempts, last_error=error[:300])
        else:
            delay = timedelta(minutes=2 ** attempts)   # 2, 4, 8, 16 minutes
            self.mark(job["_id"], status="retry", attempts=attempts, last_error=error[:300],
                      next_attempt_at=now() + delay)

    def due(self, limit=25):
        return list(self.col.find({"status": {"$in": ["queued", "retry"]},
                                   "next_attempt_at": {"$lte": now()}}).limit(limit))

    def for_user(self, user_id, limit=10):
        return list(self.col.find({"user_id": str(user_id)}, {"html": 0, "text": 0})
                    .sort("created_at", DESCENDING).limit(limit))

    def recent(self, status=None, limit=50):
        flt = {"status": status} if status else {}
        return list(self.col.find(flt, {"html": 0, "text": 0}).sort("created_at", DESCENDING).limit(limit))

    def counts(self):
        out = {}
        for s in ("queued", "retry", "sent", "failed", "skipped"):
            out[s] = self.col.count_documents({"status": s})
        return out


class TokenRepository:
    def __init__(self, col):
        self.col = col
        col.create_index("hash", unique=True)

    @staticmethod
    def _hash(raw):
        return hashlib.sha256(raw.encode()).hexdigest()

    def issue(self, user_id, kind, raw, ttl_hours):
        self.col.insert_one({"hash": self._hash(raw), "user_id": str(user_id), "kind": kind,
                             "expires_at": now() + timedelta(hours=ttl_hours), "used": False})

    def consume(self, raw, kind):
        """Single-use. Returns user_id or None."""
        doc = self.col.find_one_and_update(
            {"hash": self._hash(raw), "kind": kind, "used": False, "expires_at": {"$gte": now()}},
            {"$set": {"used": True}})
        return doc["user_id"] if doc else None


class LogRepository:
    def __init__(self, col):
        self.col = col
        col.create_index([("created_at", DESCENDING)])

    def add(self, **fields):
        self.col.insert_one({"created_at": now(), **fields})

    def recent(self, limit=50):
        return list(self.col.find({}, {"_id": 0}).sort("created_at", DESCENDING).limit(limit))

    def count_since(self, since):
        return self.col.count_documents({"created_at": {"$gte": since}})
