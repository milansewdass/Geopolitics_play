"""Deterministic tests for the rules engine (setup, dice, combat, conquest, turns, NPCs)."""
import random

import pytest

from app.catalog import CATALOG, soldiers_for_population
from app.engine import BattleEngine, GameEngine, GameError
from app.npc import StrategicNPC, conquest_gain
from app.odds import win_probability


class Scripted:
    """RNG that returns scripted die values in order."""
    def __init__(self, values): self.values = list(values)
    def randint(self, a, b): return self.values.pop(0)


def human_game(seed=0, strong=True):
    """A game on the human's first turn (searches seeds so the human has not been eliminated)."""
    for s in range(seed, seed + 200):
        e = GameEngine(random.Random(s))
        g = e.new_game("u1", "ZA")
        if g.status == "active" and not g.human.eliminated and g.turn == g.human_id:
            if strong:
                g.territories["ZA"].soldiers = 12
            return e, g
    raise AssertionError("no usable seed")


def enemy(g, minimum=0):
    return next(t for t in g.enemies_of(g.human_id) if t.soldiers >= minimum)


# ---------------------------------------------------------------- setup ----
@pytest.mark.parametrize("people,expected", [
    (1, 1), (5_000_000, 1), (5_000_001, 2), (10_000_000, 2), (10_000_001, 3), (25_000_000, 3),
    (25_000_001, 4), (50_000_000, 4), (50_000_001, 5), (75_000_000, 5), (75_000_001, 6),
    (100_000_000, 6), (100_000_001, 7), (125_000_000, 7), (125_000_001, 8), (150_000_000, 8),
    (150_000_001, 9), (1_400_000_000, 9)])
def test_population_thresholds_inclusive_upper_bounds(people, expected):
    assert soldiers_for_population(people) == expected


def test_catalogue_matches_table_and_records_source():
    from app.catalog import POPULATION_SOURCE
    assert all(c["soldiers"] == soldiers_for_population(c["population"]) for c in CATALOG.values())
    assert "worldometers" in POPULATION_SOURCE["url"] and POPULATION_SOURCE["retrieved"]


def test_setup_chosen_nation_plus_nine_unique_with_table_soldiers():
    e = GameEngine(random.Random(3))
    g = e.new_game("u1", "ZA")
    assert len(g.territories) == 10 and len({p.code for p in g.players}) == 10
    assert g.human.code == "ZA" and g.human.is_human
    assert sum(p.is_human for p in g.players) == 1
    for p in g.players:
        assert g.territories[p.code].base_soldiers == CATALOG[p.code]["soldiers"]


def test_turn_order_and_opponents_are_random():
    orders, opponents = set(), set()
    for s in range(25):
        g = GameEngine(random.Random(s)).new_game("u", "ZA")
        orders.add(g.human_id); opponents.add(tuple(sorted(p.code for p in g.players)))
    assert len(orders) > 3 and len(opponents) > 10


def test_unknown_nation_rejected():
    with pytest.raises(GameError):
        GameEngine().new_game("u", "XX")


# ------------------------------------------------------------------ dice ----
def test_worked_example_10_v_2_unsorted_pairing():
    # attacker dice [1,4] vs defender [2,3] paired in rolled order: 1<2 (att loses), 4>3 (def loses)
    r = BattleEngine(Scripted([1, 4, 2, 3])).roll_phase(10, 2)
    assert r["attacker_dice"] == [1, 4] and r["defender_dice"] == [2, 3]
    assert r["results"] == ["defender", "attacker"] and (r["att_loss"], r["def_loss"]) == (1, 1)


@pytest.mark.parametrize("a,d,n", [(2, 5, 1), (3, 5, 2), (10, 2, 2), (5, 5, 4), (9, 1, 1), (30, 25, 25)])
def test_dice_count_is_min_attacker_minus_one_and_defender_without_cap(a, d, n):
    r = BattleEngine(random.Random(1)).roll_phase(a, d)
    assert len(r["attacker_dice"]) == len(r["defender_dice"]) == n


def test_equal_dice_cause_no_casualty():
    r = BattleEngine(Scripted([3, 5, 3, 5])).roll_phase(3, 2)
    assert r["results"] == ["tie", "tie"] and (r["att_loss"], r["def_loss"]) == (0, 0)


def test_cannot_roll_with_a_single_attacker():
    with pytest.raises(GameError):
        BattleEngine(random.Random(1)).roll_phase(1, 4)


def test_exact_win_probability_sanity():
    assert win_probability(2, 0) == 1 and win_probability(1, 5) == 0
    assert 0.3 < win_probability(10, 10) < 0.5 and win_probability(11, 10) > win_probability(10, 10) and win_probability(10, 2) > 0.95
    assert win_probability(3, 8) < 0.1


# ---------------------------------------------------------------- combat ----
def test_attack_preconditions():
    e, g = human_game()
    mine, foe = g.territories["ZA"], enemy(g)
    with pytest.raises(GameError):
        e.start_attack(g, g.human_id, "ZA", "ZA")                       # own country
    mine.soldiers = 1
    with pytest.raises(GameError):
        e.start_attack(g, g.human_id, "ZA", foe.code)                   # needs 2 soldiers
    mine.soldiers = 6
    with pytest.raises(GameError):
        e.start_attack(g, g.human_id, foe.code, "ZA")                   # launch from someone else's
    with pytest.raises(GameError):
        e.start_attack(g, (g.human_id + 1) % 10, "ZA", foe.code)        # not that player's turn


def test_one_attack_per_turn_and_voluntary_stop_ends_turn():
    e, g = human_game()
    foe = enemy(g, 2)
    e.start_attack(g, g.human_id, "ZA", foe.code)
    with pytest.raises(GameError):
        e.start_attack(g, g.human_id, "ZA", enemy(g).code)              # battle in progress
    e.roll_phase(g, g.human_id)
    if g.battle:
        e.stop_attack(g, g.human_id)
        assert g.battle is None
        assert g.turn != g.human_id or g.round > 1                       # the turn passed on
    with pytest.raises(GameError):
        e.start_attack(g, g.human_id, "ZA", foe.code)                    # not (or no longer) our turn


def test_battle_ends_when_attacker_reaches_one_soldier():
    e, g = human_game()
    g.territories["ZA"].soldiers = 3
    foe = enemy(g); foe.soldiers = 60
    e.start_attack(g, g.human_id, "ZA", foe.code)
    _, outcome = e.blitz(g, g.human_id)
    assert outcome == "exhausted" and g.territories["ZA"].soldiers == 1 and foe.owner != g.human_id
    assert g.new_battles[-1]["outcome"] == "exhausted"


@pytest.mark.parametrize("base,expected", [(1, 2), (2, 2), (3, 2), (4, 2), (5, 2), (6, 3), (8, 4), (9, 4)])
def test_conquest_reward_uses_original_value_and_floor(base, expected):
    assert conquest_gain(base) == expected


def test_conquest_changes_owner_once_and_awards_original_value_half():
    for base, expected in ((1, 2), (9, 4)):
        e, g = human_game()
        g.territories["ZA"].soldiers = 80
        foe = enemy(g); foe.base_soldiers = base; foe.soldiers = 2
        before = len(g.owned(g.human_id))
        e.start_attack(g, g.human_id, "ZA", foe.code)
        _, outcome = e.blitz(g, g.human_id)
        assert outcome == "conquest" and foe.owner == g.human_id and foe.soldiers == expected
        assert len(g.owned(g.human_id)) == before + 1
        assert g.human.stats["territories_captured"] == 1                # credited exactly once
        assert sum(1 for ev in g.new_events if ev["type"] == "conquest" and ev["actor"] == g.human_id) == 1


def test_conquered_country_cannot_launch_until_next_turn():
    e, g = human_game()
    g.territories["ZA"].soldiers = 80
    foe = enemy(g); foe.soldiers = 1
    e.start_attack(g, g.human_id, "ZA", foe.code); e.blitz(g, g.human_id)
    foe.soldiers = 5
    assert foe.captured_turn == g.turn_no
    with pytest.raises(GameError):                                       # one attack per turn anyway
        e.start_attack(g, g.human_id, foe.code, enemy(g).code)
    g.has_attacked = False                                               # isolate the lock itself
    with pytest.raises(GameError, match="next turn"):
        e.start_attack(g, g.human_id, foe.code, enemy(g).code)
    e.end_turn(g, g.human_id)                                            # NPCs play, our turn returns
    if g.turn == g.human_id and not g.human.eliminated:
        while g.pending:
            e.reinforce(g, g.human_id, "ZA", 1)
        assert foe.captured_turn != g.turn_no


def test_elimination_and_victory_for_human():
    e, g = human_game()
    for t in g.territories.values():
        if t.owner not in (g.human_id,):
            t.owner = g.human_id
    last = next(iter(g.territories.values()))
    victim = (g.human_id + 1) % 10
    last.owner = victim
    for p in g.players:
        p.eliminated = p.id not in (g.human_id, victim)
    g.territories["ZA"].soldiers = 80; last.soldiers = 2
    e.start_attack(g, g.human_id, "ZA", last.code); e.blitz(g, g.human_id)
    assert g.players[victim].eliminated and g.status == "finished" and g.winner == g.human_id
    with pytest.raises(GameError):
        e.skip_turn(g, g.human_id)


# ---------------------------------------------------- turns/reinforcement ----
def test_no_reinforcements_in_round_one_but_two_per_country_afterwards():
    e, g = human_game()
    assert g.round == 1 and g.pending == 0
    e.skip_turn(g, g.human_id)
    assert g.turn == g.human_id and g.round == 2 and not g.human.eliminated
    assert g.pending == 2 * len(g.owned(g.human_id))
    before = g.territories["ZA"].soldiers
    e.reinforce(g, g.human_id, "ZA", 1)
    assert g.territories["ZA"].soldiers == before + 1 and g.pending == 2 * len(g.owned(g.human_id)) - 1
    with pytest.raises(GameError):
        e.reinforce(g, g.human_id, enemy(g).code, 1)
    with pytest.raises(GameError):
        e.reinforce(g, g.human_id, "ZA", 99)
    with pytest.raises(GameError):
        e.start_attack(g, g.human_id, "ZA", enemy(g).code)               # place first


def test_round_ends_after_every_active_participant_and_eliminated_are_skipped():
    e, g = human_game()
    victim = (g.human_id + 3) % 10
    for t in g.owned(victim):
        t.owner = g.human_id
    g.players[victim].eliminated = True
    seen = []
    orig = e.play_npc_turn
    e.play_npc_turn = lambda game: (seen.append(game.turn), orig(game))
    e.skip_turn(g, g.human_id)
    assert victim not in seen and len(set(seen)) == len(seen)
    assert g.round == 2


def test_skip_turn_not_allowed_after_attacking():
    e, g = human_game()
    foe = enemy(g, 2)
    e.start_attack(g, g.human_id, "ZA", foe.code); e.blitz(g, g.human_id)
    with pytest.raises(GameError):
        e.skip_turn(g, g.human_id)


def test_transfer_rules():
    e, g = human_game()
    other = enemy(g); other.owner = g.human_id
    g.territories["ZA"].soldiers, other.soldiers = 6, 1
    e.transfer(g, g.human_id, "ZA", other.code, 5)
    assert (g.territories["ZA"].soldiers, other.soldiers) == (1, 6)
    with pytest.raises(GameError):
        e.transfer(g, g.human_id, "ZA", other.code, 1)                   # origin keeps >= 1
    with pytest.raises(GameError):
        e.transfer(g, g.human_id, other.code, enemy(g).code, 1)          # not controlled
    other.soldiers = 6
    foe = enemy(g, 2)
    e.start_attack(g, g.human_id, other.code, foe.code)
    with pytest.raises(GameError):
        e.transfer(g, g.human_id, other.code, "ZA", 1)                   # attack in progress


# -------------------------------------------------------------------- NPC ----
def test_every_npc_decision_has_a_visible_reason_and_moves_are_legal():
    e, g = human_game()
    for _ in range(12):
        if g.status != "active" or g.human.eliminated:
            break
        while g.pending:
            e.reinforce(g, g.human_id, g.owned(g.human_id)[0].code, 1)
        e.end_turn(g, g.human_id)
    npc_events = [ev for ev in g.new_events if ev["actor"] != g.human_id]
    assert npc_events
    for ev in npc_events:
        if ev["type"] in ("attack", "hold", "reinforce"):
            assert ev.get("why"), ev
    for t in g.territories.values():
        assert t.soldiers >= 1 and 0 <= t.owner < 10


def test_npc_strategy_attacks_good_odds_and_holds_bad_odds():
    e, g = human_game()
    npc = StrategicNPC()
    pid = (g.human_id + 1) % 10
    for t in g.territories.values():
        t.soldiers = 1
    mine = g.owned(pid)[0]
    mine.soldiers = 10
    plan, why = npc.choose_attack(g, pid)
    assert plan and plan.p >= 0.6 and plan.why
    for t in g.enemies_of(pid):
        t.soldiers = 30
    plan, why = npc.choose_attack(g, pid)
    assert plan is None and "threshold" in why


def test_npc_withdraws_when_odds_collapse():
    e, g = human_game()
    pid = (g.human_id + 1) % 10
    a = g.owned(pid)[0]; d = enemy(g)
    a.soldiers, d.soldiers = 3, 20
    cont, why = StrategicNPC().should_continue(g, {"from": a.code, "to": d.code})
    assert not cont and "win chance" in why


def test_fast_forward_requires_elimination_and_finishes_game():
    e, g = human_game()
    with pytest.raises(GameError):
        e.fast_forward(g)
    for t in g.owned(g.human_id):
        t.owner = (g.human_id + 1) % 10
    g.human.eliminated = True
    for _ in range(40):
        if g.status == "finished":
            break
        e.fast_forward(g)
    assert g.status == "finished" and g.winner != g.human_id
    assert any(ev["type"] == "victory" for ev in g.recent)


@pytest.mark.parametrize("seed", range(8))
def test_npc_only_games_always_finish_and_keep_invariants(seed):
    e = GameEngine(random.Random(seed)); g = e.new_game("u", "ZA")
    for t in g.owned(g.human_id):
        t.owner = (g.human_id + 1) % 10
    g.human.eliminated = True
    for _ in range(40):
        if g.status == "finished":
            break
        e.run_npcs(g, 400)
        assert all(t.soldiers >= 1 for t in g.territories.values())
    assert g.status == "finished"
    assert len({t.owner for t in g.territories.values()}) == 1
