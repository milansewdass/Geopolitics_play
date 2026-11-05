"""Exact win probabilities for the dice battle (used by NPC strategy and shown in the UI).

State = (A, D): attacker soldiers in the launching territory, defender soldiers.
A phase rolls n = min(A-1, D) pairs; each pair independently: attacker wins 15/36, defender wins
15/36, tie 6/36. The battle ends when D == 0 (attacker conquers) or A == 1 (attacker stalled).
Solved exactly by dynamic programming; beyond EXACT_LIMIT a normal approximation (fitted to the
exact table, see tests) keeps it fast for very large stacks.
"""
from __future__ import annotations

import math
from functools import lru_cache

P_WIN = 15 / 36
P_TIE = 6 / 36
EXACT_LIMIT = 40
APPROX_K = 1.0  # z = (A-1-D) / (K * sqrt(A-1+D)); fitted against the exact DP (max error < 0.01)


@lru_cache(maxsize=None)
def _exact(a: int, d: int) -> float:
    if d <= 0:
        return 1.0
    if a <= 1:
        return 0.0
    n = min(a - 1, d)
    total = 0.0
    stay = 0.0
    for i in range(n + 1):            # attacker pair wins
        for j in range(n - i + 1):    # defender pair wins
            ties = n - i - j
            p = math.comb(n, i) * math.comb(n - i, j) * (P_WIN ** i) * (P_WIN ** j) * (P_TIE ** ties)
            if i == 0 and j == 0:
                stay = p              # nothing happened: same state again
            else:
                total += p * _exact(a - j, d - i)
    return total / (1.0 - stay)       # condition on the phase having casualties


def _phi(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def win_probability(attacker_soldiers: int, defender_soldiers: int) -> float:
    """P(attacker conquers) when attacking with `attacker_soldiers` against `defender_soldiers`."""
    a, d = int(attacker_soldiers), int(defender_soldiers)
    if d <= 0:
        return 1.0
    if a <= 1:
        return 0.0
    if a <= EXACT_LIMIT and d <= EXACT_LIMIT:
        return _exact(a, d)
    avail = a - 1
    return _phi((avail - d) / (APPROX_K * math.sqrt(avail + d)))
