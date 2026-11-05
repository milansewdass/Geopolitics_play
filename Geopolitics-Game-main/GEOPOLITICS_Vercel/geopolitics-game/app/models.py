"""Domain model: Territory, Player and Game (plain classes, serialisable to MongoDB)."""
from __future__ import annotations

from datetime import datetime, timezone

RECENT_EVENT_LIMIT = 100
COMMENTARY_LIMIT = 30


def now():
    return datetime.now(timezone.utc)


def blank_stats():
    return {"battles_won": 0, "battles_lost": 0, "territories_captured": 0,
            "soldiers_lost": 0, "soldiers_killed": 0}


class Territory:
    def __init__(self, code, name, flag, population, base_soldiers, owner, soldiers, captured_turn=-1):
        self.code = code
        self.name = name
        self.flag = flag
        self.population = population
        self.base_soldiers = base_soldiers   # ORIGINAL soldier value from the population table
        self.owner = owner                   # player id
        self.soldiers = soldiers
        self.captured_turn = captured_turn   # turn counter when last conquered (launch lock)

    def to_dict(self):
        return dict(self.__dict__)

    @classmethod
    def from_dict(cls, d):
        return cls(d["code"], d["name"], d["flag"], d["population"], d["base_soldiers"],
                   d["owner"], d["soldiers"], d.get("captured_turn", -1))


class Player:
    def __init__(self, pid, code, name, is_human, eliminated=False, stats=None):
        self.id = pid          # index in turn order
        self.code = code       # the nation this participant started as
        self.name = name
        self.is_human = is_human
        self.eliminated = eliminated
        self.stats = stats or blank_stats()

    def to_dict(self):
        return dict(self.__dict__)

    @classmethod
    def from_dict(cls, d):
        return cls(d["id"], d["code"], d["name"], d["is_human"], d.get("eliminated", False), d.get("stats"))


class Game:
    def __init__(self, owner_id, players, territories, human_id, share_code, game_id=None,
                 status="active", round_no=1, turn=0, turn_no=0, pending=0, has_attacked=False,
                 battle=None, winner=None, recent=None, commentary=None, event_seq=0, battle_no=0,
                 version=0, created_at=None, updated_at=None):
        self.id = game_id
        self.owner_id = owner_id
        self.players = players              # list[Player] in turn order (index == id)
        self.territories = territories      # dict[str, Territory]
        self.human_id = human_id
        self.share_code = share_code
        self.status = status                # active | finished
        self.round = round_no
        self.turn = turn                    # id of the participant whose turn it is
        self.turn_no = turn_no              # total turns taken so far
        self.pending = pending              # reinforcements still to place this turn
        self.has_attacked = has_attacked
        self.battle = battle                # active battle dict or None
        self.winner = winner
        self.recent = recent or []          # last N events (full history is in `events`)
        self.commentary = commentary or []
        self.event_seq = event_seq
        self.battle_no = battle_no
        self.version = version
        self.created_at = created_at or now()
        self.updated_at = updated_at or now()
        self.new_events = []                # transient: events produced by this request
        self.new_battles = []               # transient: finished battle records

    # ---- queries -------------------------------------------------------
    @property
    def human(self):
        return self.players[self.human_id]

    def owned(self, pid):
        return [t for t in self.territories.values() if t.owner == pid]

    def enemies_of(self, pid):
        return [t for t in self.territories.values() if t.owner != pid]

    def alive_players(self):
        return [p for p in self.players if not p.eliminated]

    def snapshot(self, codes):
        return {c: {"owner": self.territories[c].owner, "soldiers": self.territories[c].soldiers}
                for c in codes}

    # ---- persistence ---------------------------------------------------
    def to_doc(self):
        doc = {
            "owner_id": self.owner_id, "human_id": self.human_id,
            "players": [p.to_dict() for p in self.players],
            "territories": {k: t.to_dict() for k, t in self.territories.items()},
            "share_code": self.share_code, "status": self.status, "round": self.round,
            "turn": self.turn, "turn_no": self.turn_no, "pending": self.pending,
            "has_attacked": self.has_attacked, "battle": self.battle, "winner": self.winner,
            "recent": self.recent[-RECENT_EVENT_LIMIT:], "commentary": self.commentary[-COMMENTARY_LIMIT:],
            "event_seq": self.event_seq, "battle_no": self.battle_no, "version": self.version,
            "created_at": self.created_at, "updated_at": self.updated_at,
        }
        if self.id is not None:
            doc["_id"] = self.id
        return doc

    @classmethod
    def from_doc(cls, d):
        return cls(
            d["owner_id"], [Player.from_dict(p) for p in d["players"]],
            {k: Territory.from_dict(t) for k, t in d["territories"].items()},
            d["human_id"], d["share_code"], game_id=d.get("_id"), status=d["status"],
            round_no=d["round"], turn=d["turn"], turn_no=d.get("turn_no", 0), pending=d["pending"],
            has_attacked=d["has_attacked"], battle=d.get("battle"), winner=d.get("winner"),
            recent=d.get("recent", []), commentary=d.get("commentary", []),
            event_seq=d.get("event_seq", 0), battle_no=d.get("battle_no", 0),
            version=d.get("version", 0), created_at=d.get("created_at"), updated_at=d.get("updated_at"),
        )

    # ---- projections ---------------------------------------------------
    def view(self, is_owner):
        """Public-safe projection. Contains no account data (no usernames, e-mails or user ids).
        `is_owner` only decides whether the viewer may be offered action controls."""
        current = self.players[self.turn]
        return {
            "id": str(self.id) if is_owner else None,
            "share_code": self.share_code, "status": self.status, "version": self.version,
            "round": self.round, "turn": self.turn, "turn_no": self.turn_no,
            "current_player": current.name, "human_id": self.human_id,
            "pending": self.pending if is_owner else None,
            "has_attacked": self.has_attacked, "battle": self.battle, "winner": self.winner,
            "winner_name": self.players[self.winner].name if self.winner is not None else None,
            "players": [
                {**p.to_dict(), "territories": len(self.owned(p.id)),
                 "soldiers": sum(t.soldiers for t in self.owned(p.id))}
                for p in self.players
            ],
            "territories": [t.to_dict() for t in self.territories.values()],
            "events": self.recent[-60:], "event_seq": self.event_seq,
            "commentary": self.commentary[-8:],
            "can_act": bool(is_owner and self.status == "active" and self.turn == self.human_id
                            and not self.human.eliminated),
            "human_eliminated": self.human.eliminated,
            "is_owner": bool(is_owner),
        }
