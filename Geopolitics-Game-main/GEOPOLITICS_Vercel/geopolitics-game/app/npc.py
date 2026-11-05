"""The strategic NPC. One documented strategy is shared by all nine opponents.

Every decision is derived from the board and returns a human-readable rationale that the engine
publishes to the event feed (also while fast-forwarding).

Per turn an NPC:
  1. allocates its reinforcements greedily: each soldier goes where it adds the most expected
     attack value, with a bonus for garrisons that an enemy stack could overrun;
  2. scores every legal (launch country, target) pair with the exact win probability and attacks
     only if the best option reaches ATTACK_MIN_P;
  3. fights phase by phase and withdraws once the win chance drops below WITHDRAW_P;
  4. may move soldiers from a safe stack to a dangerously thin garrison.
"""
from __future__ import annotations

from dataclasses import dataclass

from .odds import win_probability

ATTACK_MIN_P = 0.60
WITHDRAW_P = 0.35
ELIMINATION_BONUS = 4.0


@dataclass
class AttackPlan:
    source: str
    target: str
    p: float
    why: str


def conquest_gain(base_soldiers: int) -> int:
    return max(2, base_soldiers // 2)


class StrategicNPC:
    name = "Strategist"

    # ---- evaluation helpers --------------------------------------------
    @staticmethod
    def _launchable(game, pid):
        return [t for t in game.owned(pid) if t.soldiers >= 2 and t.captured_turn != game.turn_no]

    @staticmethod
    def _target_value(game, tgt):
        v = conquest_gain(tgt.base_soldiers) + 0.2 * tgt.soldiers
        if len(game.owned(tgt.owner)) == 1:
            v += ELIMINATION_BONUS            # knocks a rival out of the game
        return v

    def _best_attack_value(self, game, pid, soldiers):
        """Best p*value an army of `soldiers` would have against the current enemies."""
        best = 0.0
        for tgt in game.enemies_of(pid):
            best = max(best, win_probability(soldiers, tgt.soldiers) * self._target_value(game, tgt))
        return best

    @staticmethod
    def _threat(game, pid):
        """Largest enemy army (launch strength) that could attack any of our countries."""
        return max((t.soldiers - 1 for t in game.enemies_of(pid)), default=0)

    # ---- reinforcements --------------------------------------------------
    def reinforce_plan(self, game, pid, count):
        """Returns ([(code, n)], rationale)."""
        mine = game.owned(pid)
        soldiers = {t.code: t.soldiers for t in mine}
        threat = self._threat(game, pid)
        added = {}
        for _ in range(count):
            best, best_score = None, -1.0
            for t in mine:
                s = soldiers[t.code]
                gain = self._best_attack_value(game, pid, s + 1) - self._best_attack_value(game, pid, s)
                if s <= threat and len(mine) > 1:
                    gain += 0.25 * min(1.0, (threat - s + 1) / max(threat, 1))   # garrison at risk
                gain += 0.001 * s                                                  # stable tie-break
                if gain > best_score:
                    best, best_score = t.code, gain
            soldiers[best] += 1
            added[best] = added.get(best, 0) + 1
        plan = list(added.items())
        names = ", ".join(f"+{n} {game.territories[c].name}" for c, n in plan)
        return plan, (f"Concentrating where extra soldiers raise attack odds most while covering weak garrisons "
                      f"(strongest enemy launch force: {threat}). Allocation: {names}.")

    # ---- attack ----------------------------------------------------------
    def choose_attack(self, game, pid):
        """Returns (AttackPlan | None, skip_reason)."""
        options = []
        for src in self._launchable(game, pid):
            for tgt in game.enemies_of(pid):
                p = win_probability(src.soldiers, tgt.soldiers)
                value = self._target_value(game, tgt)
                # defensive risk: after the expected losses can the launch country still hold?
                left = src.soldiers - min(src.soldiers - 1, 0.9 * tgt.soldiers)
                risk = max(0.0, self._threat(game, pid) - left) * 0.01
                utility = p * value - (1 - p) * 0.5 * (src.soldiers - 1) * 0.1 - risk
                options.append((utility, p, src, tgt, value))
        if not options:
            return None, "no country has 2 or more soldiers to launch an attack"
        good = [o for o in options if o[1] >= ATTACK_MIN_P]
        if not good:
            u, p, src, tgt, _ = max(options, key=lambda o: o[1])
            return None, (f"no target reaches the {int(ATTACK_MIN_P * 100)}% win-chance threshold "
                          f"(best was {tgt.name} from {src.name} at {p:.0%})")
        u, p, src, tgt, value = max(good, key=lambda o: o[0])
        reasons = [f"{p:.0%} win chance ({src.soldiers} v {tgt.soldiers})",
                   f"capture bonus {conquest_gain(tgt.base_soldiers)} soldiers"]
        if len(game.owned(tgt.owner)) == 1:
            reasons.append(f"would eliminate {game.players[tgt.owner].name}")
        elif tgt.soldiers >= 4:
            reasons.append("breaks a strong garrison")
        return AttackPlan(src.code, tgt.code, p, "; ".join(reasons)), ""

    def should_continue(self, game, battle):
        """After a phase: (continue?, rationale)."""
        a = game.territories[battle["from"]]
        d = game.territories[battle["to"]]
        p = win_probability(a.soldiers, d.soldiers)
        if p >= WITHDRAW_P:
            return True, f"win chance still {p:.0%} ({a.soldiers} v {d.soldiers})"
        return False, f"win chance fell to {p:.0%} ({a.soldiers} v {d.soldiers}); preserving soldiers"

    # ---- transfers -------------------------------------------------------
    def plan_transfer(self, game, pid):
        """One consolidation move or None: (from, to, count, rationale)."""
        mine = game.owned(pid)
        if len(mine) < 2:
            return None
        threat = self._threat(game, pid)
        rich = max(mine, key=lambda t: t.soldiers)
        poor = min(mine, key=lambda t: t.soldiers)
        if rich is poor or poor.soldiers > 2 or poor.soldiers > threat or rich.soldiers < 6:
            return None
        count = min((rich.soldiers - poor.soldiers) // 3, rich.soldiers - 3)
        if count < 1:
            return None
        return (rich.code, poor.code, count,
                f"{poor.name} holds only {poor.soldiers} while enemies can field {threat}; "
                f"{rich.name} keeps {rich.soldiers - count}")
