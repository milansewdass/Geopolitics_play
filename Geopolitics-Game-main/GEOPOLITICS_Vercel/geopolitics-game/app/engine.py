"""Game rules engine. Pure Python (no database / web code) so it is easy to test.

Every state change appends *events* (with a before/after snapshot of the countries it touched) so the
browser can replay NPC turns step by step and the full history can be persisted and replayed later.
"""
from __future__ import annotations

import random
import secrets

from .catalog import CATALOG
from .models import Game, Player, Territory
from .npc import StrategicNPC, conquest_gain

REINFORCEMENT_PER_COUNTRY = 2
TOTAL_COUNTRIES = 10


class GameError(Exception):
    """A rule was broken (maps to HTTP 400)."""


class BattleEngine:
    """Rolls one combat phase. Deterministic given the RNG; prose never touches this."""

    def __init__(self, rng):
        self.rng = rng

    def roll_phase(self, attacker_soldiers, defender_soldiers):
        # Both sides roll the same number of dice: min(attacker - 1, defender). No cap.
        n = min(attacker_soldiers - 1, defender_soldiers)
        if n < 1:
            raise GameError("Not enough soldiers to roll.")
        a = [self.rng.randint(1, 6) for _ in range(n)]
        d = [self.rng.randint(1, 6) for _ in range(n)]
        att_loss = def_loss = 0
        results = []
        for x, y in zip(a, d):          # pair i with i, no sorting
            if x > y:
                def_loss += 1
                results.append("attacker")
            elif y > x:
                att_loss += 1
                results.append("defender")
            else:
                results.append("tie")
        return {"attacker_dice": a, "defender_dice": d, "results": results,
                "att_loss": att_loss, "def_loss": def_loss}


class GameEngine:
    def __init__(self, rng=None, npc=None):
        self.rng = rng or random.Random()
        self.dice = BattleEngine(self.rng)
        self.npc = npc or StrategicNPC()

    # ------------------------------------------------------------------
    # Events
    # ------------------------------------------------------------------
    @staticmethod
    def _emit(game, etype, text, why=None, touched=(), data=None, actor=None):
        game.event_seq += 1
        ev = {"seq": game.event_seq, "round": game.round, "turn_no": game.turn_no,
              "actor": game.turn if actor is None else actor, "type": etype, "text": text}
        if why:
            ev["why"] = why
        if touched:
            ev["delta"] = game.snapshot(touched)
        if data:
            ev["data"] = data
        game.recent.append(ev)
        del game.recent[:-200]
        game.new_events.append(ev)
        return ev

    # ------------------------------------------------------------------
    # Setup
    # ------------------------------------------------------------------
    def new_game(self, owner_id, nation_code):
        if nation_code not in CATALOG:
            raise GameError("Unknown nation.")
        others = self.rng.sample([c for c in CATALOG if c != nation_code], TOTAL_COUNTRIES - 1)
        order = [nation_code] + others
        self.rng.shuffle(order)                       # random initial turn order
        human_id = order.index(nation_code)
        players, territories = [], {}
        for pid, code in enumerate(order):
            c = CATALOG[code]
            players.append(Player(pid, code, c["name"], pid == human_id))
            territories[code] = Territory(code, c["name"], c["flag"], c["population"],
                                          c["soldiers"], pid, c["soldiers"])
        game = Game(owner_id, players, territories, human_id, secrets.token_urlsafe(6))
        names = " → ".join(p.name for p in players)
        self._emit(game, "setup", f"Campaign begins with {CATALOG[nation_code]['name']}. Turn order: {names}.",
                   touched=list(territories), actor=None)
        self._begin_turn(game)
        self.run_npcs(game, TOTAL_COUNTRIES)
        return game

    # ------------------------------------------------------------------
    # Guards
    # ------------------------------------------------------------------
    @staticmethod
    def _require_turn(game, pid):
        if game.status != "active":
            raise GameError("The game is over.")
        if game.players[pid].eliminated:
            raise GameError("You have been eliminated.")
        if game.turn != pid:
            raise GameError("It is not your turn.")

    @staticmethod
    def _territory(game, code):
        t = game.territories.get(code)
        if t is None:
            raise GameError("Unknown country.")
        return t

    # ------------------------------------------------------------------
    # Actions (player or NPC)
    # ------------------------------------------------------------------
    def reinforce(self, game, pid, code, count, why=None, _emit=True):
        self._require_turn(game, pid)
        if game.battle:
            raise GameError("Finish the current battle first.")
        t = self._territory(game, code)
        if t.owner != pid:
            raise GameError("You can only reinforce your own countries.")
        if isinstance(count, bool) or not isinstance(count, int) or count < 1:
            raise GameError("Soldier count must be a positive whole number.")
        if count > game.pending:
            raise GameError(f"You only have {game.pending} reinforcement(s) to place.")
        t.soldiers += count
        game.pending -= count
        if _emit:
            self._emit(game, "reinforce", f"{game.players[pid].name} places {count} soldier(s) in {t.name}.",
                       why=why, touched=[code])

    def start_attack(self, game, pid, frm, to, why=None):
        self._require_turn(game, pid)
        if game.battle:
            raise GameError("A battle is already in progress.")
        if game.pending > 0:
            raise GameError(f"Place your {game.pending} reinforcement(s) first.")
        if game.has_attacked:
            raise GameError("Only one attack may be initiated per turn.")
        a, d = self._territory(game, frm), self._territory(game, to)
        if a.owner != pid:
            raise GameError("You must attack from a country you control.")
        if d.owner == pid:
            raise GameError("You cannot attack your own country.")
        if a.soldiers < 2:
            raise GameError("Attacking needs at least 2 soldiers (1 must stay behind).")
        if a.captured_turn == game.turn_no:
            raise GameError("A country conquered this turn can launch attacks from your next turn.")
        game.has_attacked = True
        game.battle_no += 1
        game.battle = {"no": game.battle_no, "from": frm, "to": to, "attacker": pid, "defender": d.owner,
                       "att_start": a.soldiers, "def_start": d.soldiers, "att_lost": 0, "def_lost": 0,
                       "phases": []}
        self._emit(game, "attack", f"{game.players[pid].name} attacks {d.name} from {a.name} "
                                   f"({a.soldiers} v {d.soldiers}).", why=why, touched=[frm, to],
                   data={"battle_no": game.battle_no, "from": frm, "to": to,
                         "attacker": pid, "defender": d.owner})

    def roll_phase(self, game, pid):
        self._require_turn(game, pid)
        b = game.battle
        if not b:
            raise GameError("There is no battle in progress.")
        a, d = game.territories[b["from"]], game.territories[b["to"]]
        phase = self.dice.roll_phase(a.soldiers, d.soldiers)
        a.soldiers -= phase["att_loss"]
        d.soldiers -= phase["def_loss"]
        b["att_lost"] += phase["att_loss"]
        b["def_lost"] += phase["def_loss"]
        phase["no"] = len(b["phases"]) + 1
        phase["att_after"], phase["def_after"] = a.soldiers, d.soldiers
        b["phases"].append(phase)
        att_p, def_p = game.players[b["attacker"]], game.players[b["defender"]]
        att_p.stats["soldiers_lost"] += phase["att_loss"]
        att_p.stats["soldiers_killed"] += phase["def_loss"]
        def_p.stats["soldiers_lost"] += phase["def_loss"]
        def_p.stats["soldiers_killed"] += phase["att_loss"]
        pairs = "; ".join(f"{x} v {y}" for x, y in zip(phase["attacker_dice"], phase["defender_dice"]))
        self._emit(game, "phase",
                   f"Phase {phase['no']}: {pairs}. {att_p.name} loses {phase['att_loss']}, "
                   f"{def_p.name} loses {phase['def_loss']}. Now {a.soldiers} v {d.soldiers}.",
                   touched=[b["from"], b["to"]],
                   data={"battle_no": b["no"], "from": b["from"], "to": b["to"], **phase})
        outcome = None
        if d.soldiers <= 0:
            outcome = "conquest"
        elif a.soldiers <= 1:
            outcome = "exhausted"
        if outcome:
            self._finish_battle(game, outcome)
        return phase, outcome

    def blitz(self, game, pid):
        """Keep rolling until the battle ends (human convenience)."""
        phases, outcome = [], None
        while game.battle:
            phase, outcome = self.roll_phase(game, pid)
            phases.append(phase)
        return phases, outcome

    def withdraw(self, game, pid, why=None):
        self._require_turn(game, pid)
        if not game.battle:
            raise GameError("There is no battle to stop.")
        self._finish_battle(game, "withdrawn", why=why)

    def stop_attack(self, game, pid):
        """Player stops a battle voluntarily; this ends their turn."""
        self.withdraw(game, pid)
        self.end_turn(game, pid, _text="stops the attack and ends the turn")

    def transfer(self, game, pid, frm, to, count, why=None):
        self._require_turn(game, pid)
        if game.battle:
            raise GameError("Soldiers cannot be moved while an attack is in progress.")
        a, b = self._territory(game, frm), self._territory(game, to)
        if a.owner != pid or b.owner != pid:
            raise GameError("Soldiers can only move between countries you own.")
        if frm == to:
            raise GameError("Choose two different countries.")
        if isinstance(count, bool) or not isinstance(count, int) or count < 1:
            raise GameError("Soldier count must be a positive whole number.")
        if a.soldiers - count < 1:
            raise GameError("The origin country must keep at least 1 soldier.")
        a.soldiers -= count
        b.soldiers += count
        self._emit(game, "transfer", f"{game.players[pid].name} moves {count} soldier(s) from {a.name} to {b.name}.",
                   why=why, touched=[frm, to])

    def skip_turn(self, game, pid):
        self._require_turn(game, pid)
        if game.battle:
            raise GameError("Finish or stop the current battle first.")
        if game.has_attacked:
            raise GameError("You already attacked this turn; end your turn instead.")
        self.end_turn(game, pid, _text="skips the turn")

    def end_turn(self, game, pid, _text=None):
        self._require_turn(game, pid)
        if game.battle:
            raise GameError("Finish or stop the current battle first.")
        self._auto_place(game, pid)
        self._emit(game, "end", f"{game.players[pid].name} {_text or 'ends the turn'}.")
        self.advance_turn(game)
        self.run_npcs(game, len(game.players))

    # ------------------------------------------------------------------
    # Turn machinery
    # ------------------------------------------------------------------
    def _auto_place(self, game, pid):
        placed = {}
        while game.pending > 0:
            weakest = min(game.owned(pid), key=lambda t: t.soldiers)
            weakest.soldiers += 1
            game.pending -= 1
            placed[weakest.code] = placed.get(weakest.code, 0) + 1
        if placed:
            names = ", ".join(f"+{n} {game.territories[c].name}" for c, n in placed.items())
            self._emit(game, "reinforce", f"Unplaced reinforcements were assigned automatically: {names}.",
                       touched=list(placed))

    def advance_turn(self, game):
        n = len(game.players)
        idx = game.turn
        for _ in range(n + 1):
            idx += 1
            if idx >= n:
                idx = 0
                game.round += 1
            if not game.players[idx].eliminated:
                break
        game.turn = idx
        self._begin_turn(game)

    def _begin_turn(self, game):
        game.turn_no += 1
        game.has_attacked = False
        game.battle = None
        pid = game.turn
        owned = len(game.owned(pid))
        game.pending = REINFORCEMENT_PER_COUNTRY * owned if game.round >= 2 else 0
        extra = f" and receives {game.pending} reinforcements ({owned} countries × 2)" if game.pending else ""
        self._emit(game, "turn", f"Round {game.round}: {game.players[pid].name}'s turn{extra}.",
                   data={"pending": game.pending})

    def run_npcs(self, game, max_turns):
        """Play NPC turns until a living human is up, the game ends or max_turns is hit."""
        played = 0
        while game.status == "active" and played < max_turns:
            cur = game.players[game.turn]
            if cur.is_human and not cur.eliminated:
                break
            self.play_npc_turn(game)
            if game.status != "active":
                break
            self.advance_turn(game)
            played += 1
        return played

    def play_npc_turn(self, game):
        pid = game.turn
        name = game.players[pid].name
        npc = self.npc
        # 1. reinforcements
        if game.pending > 0:
            plan, why = npc.reinforce_plan(game, pid, game.pending)
            for code, n in plan:
                self.reinforce(game, pid, code, n, _emit=False)
            self._emit(game, "reinforce", f"{name} allocates reinforcements: "
                       + ", ".join(f"+{n} {game.territories[c].name}" for c, n in plan) + ".",
                       why=why, touched=[c for c, _ in plan])
        # 2. one attack, or hold
        plan, reason = npc.choose_attack(game, pid)
        if plan:
            self.start_attack(game, pid, plan.source, plan.target, why=plan.why)
            while game.battle:
                if game.battle["phases"]:
                    cont, why = npc.should_continue(game, game.battle)
                    if not cont:
                        self._finish_battle(game, "withdrawn", why=why)
                        break
                self.roll_phase(game, pid)
        else:
            self._emit(game, "hold", f"{name} holds position and does not attack.", why=reason)
        # 3. consolidation
        if game.status == "active":
            move = npc.plan_transfer(game, pid)
            if move:
                frm, to, count, why = move
                try:
                    self.transfer(game, pid, frm, to, count, why=why)
                except GameError:
                    pass
        if game.status == "active":
            self._emit(game, "end", f"{name} ends the turn.")

    # ------------------------------------------------------------------
    # Battle resolution
    # ------------------------------------------------------------------
    def _finish_battle(self, game, outcome, why=None):
        b = game.battle
        game.battle = None
        a, d = game.territories[b["from"]], game.territories[b["to"]]
        att_p, def_p = game.players[b["attacker"]], game.players[b["defender"]]
        record = {
            "battle_no": b["no"], "round": game.round, "attacker": att_p.name, "defender": def_p.name,
            "from": a.name, "to": d.name, "from_code": b["from"], "to_code": b["to"],
            "att_start": b["att_start"], "def_start": b["def_start"], "att_lost": b["att_lost"],
            "def_lost": b["def_lost"], "att_end": a.soldiers, "def_end": d.soldiers,
            "phases": b["phases"], "outcome": outcome, "human_involved": att_p.is_human or def_p.is_human,
        }
        n_phases = len(b["phases"])
        touched = [b["from"], b["to"]]
        if outcome == "conquest":
            att_p.stats["battles_won"] += 1
            def_p.stats["battles_lost"] += 1
            att_p.stats["territories_captured"] += 1
            gain = conquest_gain(d.base_soldiers)       # based on the ORIGINAL soldier value, awarded once
            d.owner = att_p.id
            d.soldiers = gain
            d.captured_turn = game.turn_no
            record["gain"] = gain
            self._emit(game, "conquest", f"{att_p.name} conquers {d.name} after {n_phases} phase(s) and "
                       f"garrisons it with {gain} new soldiers.", touched=touched,
                       data={"battle_no": b["no"], "gain": gain})
            if not game.owned(def_p.id):
                def_p.eliminated = True
                self._emit(game, "elimination", f"{def_p.name} has been eliminated.", actor=def_p.id)
            if len(game.owned(att_p.id)) == len(game.territories):
                game.status = "finished"
                game.winner = att_p.id
                self._emit(game, "victory", f"{att_p.name} controls all {len(game.territories)} countries "
                           f"and wins the game.", actor=att_p.id)
        elif outcome == "exhausted":
            def_p.stats["battles_won"] += 1
            att_p.stats["battles_lost"] += 1
            self._emit(game, "exhausted", f"{att_p.name}'s attack on {d.name} stalls with 1 soldier left; "
                       f"{d.name} holds.", touched=touched, data={"battle_no": b["no"]})
        else:
            self._emit(game, "withdraw", f"{att_p.name} stops the attack on {d.name} "
                       f"(lost {b['att_lost']}, inflicted {b['def_lost']}).", why=why, touched=touched,
                       data={"battle_no": b["no"]})
        record["summary"] = game.recent[-1]["text"]
        game.new_battles.append(record)

    # ------------------------------------------------------------------
    # Fast-forward (only once the human is out of the game)
    # ------------------------------------------------------------------
    def fast_forward(self, game, max_turns=400):
        if game.status != "active":
            raise GameError("The game is over.")
        if not game.human.eliminated:
            raise GameError("Fast-forward is only needed after you have been eliminated.")
        return self.run_npcs(game, max_turns)
