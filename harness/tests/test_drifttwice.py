"""`jevbench drifttwice` (#37): which of a row's stored answer and two new ones is the odd one out?"""
import math
from math import comb

import numpy as np

from jevbench import drifttwice as T
from pgdocket import drift


def test_the_odd_answer_is_the_one_the_two_others_disagree_with():
    a0 = np.array([1, 1, 1, 2, 1, 1])
    a1 = np.array([1, 2, 1, 1, 2, -1])
    a2 = np.array([1, 2, 2, 1, 3, -1])
    #            all agree, stored odd, second new odd, stored odd, all differ, stored odd (both new ones "another answer")
    assert T.odd_answers(a0, a1, a2).tolist() == [-1, 0, 2, 0, -1, 0]
    assert T.odd_answers(a0, a2, a1).tolist() == [-1, 0, 1, 0, -1, 0]


def test_the_odd_score_is_the_one_left_out_by_the_closest_pair():
    s0 = np.array([0.9, 0.5, 0.90, 0.2, 0.4])
    s1 = np.array([0.9, 0.8, 0.90, 0.5, 0.6])
    s2 = np.array([0.9, 0.8, 0.70, 0.9, 0.8])
    #             all equal, stored odd, second new odd, first two closest → third odd, evenly spaced → none
    assert T.odd_scores(s0, s1, s2).tolist() == [-1, 0, 2, 2, -1]


def test_the_bet_is_fair_when_the_stored_answer_is_odd_a_third_of_the_time():
    for n in (1, 7, 40):
        pmf = [comb(n, k) * (1 / 3) ** k * (2 / 3) ** (n - k) for k in range(n + 1)]
        assert math.isclose(sum(p * math.exp(float(T.log_odd(k, n))) for k, p in enumerate(pmf)), 1.0, rel_tol=1e-9)
    assert T.log_odd(30, 36) > math.log(10 ** 6) and T.log_odd(12, 36) < 0      # 83 % against a third; a third exactly
    assert math.isclose(float(T.log_odd(9, 12, chance=0.5)), drift.log_bets(9, 3))   # at one chance in two: the sign bets
    k, n = np.array([[3, 30], [12, 0]]), np.array([[9, 36], [36, 0]])            # arrays: one value per cell
    assert T.log_odd(k, n).shape == (2, 2) and T.log_odd(k, n)[1, 1] == 0


def test_the_margin_bet_cannot_grow_while_the_change_stays_within_the_margin():
    m, rows = T.MIN_CHANGED, 6
    for q in (0.0, 0.02, 0.2):                    # a new answer is the odd one with chance q each; the stored one, q + m
        probs = (q + m, 2 * q, 1 - 3 * q - m)
        total = 0.0
        for k in range(rows + 1):
            for j in range(rows + 1 - k):
                p = (math.factorial(rows) / (math.factorial(k) * math.factorial(j) * math.factorial(rows - k - j))
                     * probs[0] ** k * probs[1] ** j * probs[2] ** (rows - k - j))
                total += p * math.exp(float(T.log_margin(k, k + j, rows)))
        assert total <= 1 + 1e-9
    assert T.log_margin(40, 44, 200) > math.log(1000) and T.log_margin(4, 12, 200) < 0


def test_the_exact_test_against_a_third():
    assert T.binom_two_sided(0, 0) == 1.0 and math.isclose(T.binom_two_sided(31, 93), 1.0)   # the likeliest count
    assert math.isclose(T.binom_two_sided(43, 93), 0.011, abs_tol=0.0005)       # the CFPB pairing that rejects
    assert math.isclose(T.binom_two_sided(3, 3), (1 / 3) ** 3)


def test_the_designs_hold_the_rows_or_the_calls_fixed():
    d = T.designs(100)
    assert d["once"] == (100, False) and d["twice, half the rows"] == (50, True)      # the same calls
    assert d["twice"] == (100, True) and d["once, twice the rows"] == (200, False)    # the same calls again


def _task(change_share, rng, rows=600, options=6):
    """A model that never flips, and a change that gives a share of rows another answer and moves no score."""
    a = rng.integers(0, options, rows)
    changed = np.where(rng.random(rows) < change_share, (a + rng.integers(1, options, rows)) % options, a)
    s = np.full(rows, 0.9)
    return {"orig": [(a, s), (a, s), (a, s)], "truth": a, "changes": {"another answer": (changed, s)}}


def test_no_rule_alarms_when_nothing_changes_and_answers_never_flip():
    st = T.series(_task(0.0, np.random.default_rng(0)), 100, 25, 50, 8, None, 1.0, 1, 5, np.random.default_rng(1))
    assert set(st) == set(T.designs(50))
    for d in st.values():
        assert all((T.first_alarm(d, c, kind) == T.NEVER).all() for c in (0.0, *T.SHARES) for kind in T.KINDS)


def test_a_change_in_no_one_direction_shows_only_when_rows_are_asked_twice():
    task = _task(0.3, np.random.default_rng(2))
    st = T.series(task, 100, 25, 50, 8, "another answer", 1.0, 1, 20, np.random.default_rng(3))
    labelled_only = T.first_alarm(st["once"], 0.5)           # asked once: only the labelled rows can show it
    twice = T.first_alarm(st["twice, half the rows"], 0.5)
    assert (twice <= 3).all() and (twice <= labelled_only).all()
    assert (T.first_alarm(st["twice, half the rows"], 0.5, "scores") == T.first_alarm(st["twice, half the rows"], 0.0)).all()
    r = T.rates(st, 1, 8)
    assert r["twice"]["answers · 0.50"]["end"] == 100.0 and set(r["once"]) == {"today"} | {
        f"{kind} · {c:.2f}" for c in T.SHARES for kind in T.KINDS}


def test_the_share_is_the_one_with_the_smallest_worst_shortfall():
    cell = lambda a, b, c: {"once": {f"answers · {s:.2f}": {"at": x, "end": x} for s, x in zip(T.SHARES, (a, b, c))}}
    chosen, worst, mean, where = T.choose({"task · a change": cell(50, 48, 40), "task · another": cell(10, 30, 31),
                                           "task · no change · from check 1": cell(0, 50, 100)})
    assert chosen == T.SHARES[1] and worst[T.SHARES[0]] == 21 and worst[T.SHARES[2]] == 10 and worst[chosen] == 2
    assert "no change" not in where[T.SHARES[0]]


def test_a_triple_counts_the_odd_one_out_by_its_place():
    """`jevbench twicerepeat` (#38): which of three runs is the odd one out, for answers and for scores."""
    from jevbench import twicerepeat
    R = {"s": (np.array([1, 1, 2, 3, 1]), np.array([0.9, 0.5, 0.7, 0.2, 0.6])),
         "x": (np.array([2, 1, 2, 3, 1]), np.array([0.8, 0.8, 0.7, 0.5, 0.6])),
         "y": (np.array([2, 1, 1, 4, 1]), np.array([0.8, 0.8, 0.4, 0.9, 0.6]))}
    t = twicerepeat.triple(R, "s", "x", "y")
    # answers: row 0 sets the stored one apart, rows 2 and 3 the second re-ask; rows 1 and 4 all agree
    assert (t["answers"]["first"], t["answers"]["second"], t["answers"]["third"], t["answers"]["of"]) == (1, 0, 2, 3)
    assert (t["scores"]["first"], t["scores"]["of"]) == (2, 4)          # rows 0 and 1 set the stored score apart
    assert t["answers"]["p"] == T.binom_two_sided(1, 3)
