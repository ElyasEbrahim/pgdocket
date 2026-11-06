"""`jevbench driftsplit` (#34) compares budget shares through four statistics per check instead of rerunning the rows
check for every share. That shortcut is only worth anything if it alarms exactly when `drift.rows_check` does."""
import numpy as np
import pytest

from jevbench import driftsplit
from pgdocket import drift


def _rows(seed=7, n=600):
    rng = np.random.default_rng(seed)
    answers = ["billing", "sales", "technical"]
    rows = []
    for i in range(n):
        label = str(rng.choice(answers, p=[0.5, 0.3, 0.2]))
        answer = label if rng.random() < 0.85 else str(rng.choice(answers))
        rows.append((str(i), answer, float(np.round(rng.beta(4, 2), 2)), label))
    return rows


@pytest.mark.parametrize("w", [0.5, drift.WINDOW_SHARE])
def test_the_statistics_alarm_exactly_when_the_rows_check_does(w):
    in_use = drift.WINDOW_SHARE
    same, total, alarms = driftsplit.verify(_rows(), np.random.default_rng(3), runs=5, b=60, checks=6, delta=0.3, w=w)
    assert same == total and alarms > 0
    assert drift.WINDOW_SHARE == in_use                  # verify puts the share in use back


def test_the_budget_still_adds_up_to_alpha():
    # the window tests over every check, plus the evidence once, never spend more than ALPHA
    k = np.arange(1, 200001, dtype=float)
    window = float((drift.WINDOW_SHARE * drift.ALPHA * 6 / (np.pi ** 2 * k ** 2)).sum())
    evidence = 2 * (1 - drift.WINDOW_SHARE) * drift.ALPHA / 2
    assert window + evidence <= drift.ALPHA + 1e-12
    assert abs(window + evidence - drift.ALPHA) < 1e-6


def test_choose_takes_the_smallest_largest_regret_then_the_smallest_mean():
    ws = driftsplit.WS
    scen = {"fresh": {w: 50 + 40 * w for w in ws},              # a single check: the bigger the window share the better
            "lasting": {w: 90 - 100 * max(0.0, w - 0.6) for w in ws}}   # a long series: the evidence matters
    chosen, worst, mean, _ = driftsplit.choose(scen)
    lo = min(worst.values())
    assert worst[chosen] <= lo + 1.0
    assert all(mean[chosen] <= mean[w] for w in ws if worst[w] <= lo + 1.0)
