"""`jevbench auditbets` (#42): the vectorised bets match pgdocket's, and fresh starts stay valid."""
import math

import numpy as np

from jevbench import auditbets as AB
from pgdocket.metrics import eprocess_below


def test_the_vectorised_bets_are_the_audits_own():
    rng = np.random.default_rng(42)
    X = (rng.uniform(size=(5, 300)) < np.r_[np.full(100, 0.95), np.full(200, 0.7)]).astype(float)
    lw = AB.agrapa_logw(X, 0.85)
    for r in range(5):
        assert np.allclose(np.exp(lw[r]), eprocess_below(X[r], 0.85, window=40))
        assert np.allclose(np.exp(AB.agrapa_logw(X[r:r + 1], 0.85, window=20)[0]), eprocess_below(X[r], 0.85))   # #42
    later = AB.agrapa_logw(X, 0.85, start=100)
    assert (later[:, :100] == 0).all()
    assert np.allclose(np.exp(later[0, 100:]), eprocess_below(X[0, 100:], 0.85, window=40))


def test_fresh_starts_rarely_alarm_at_the_target():
    """Each start is a test martingale at exactly the target, and the weights sum to 1, so by Ville's inequality the
    mixture reaches 20 in at most 5 % of audits. (Its mean wealth is 1, but the tail is too heavy to check that here:
    a few audits end in the hundreds.)"""
    rng = np.random.default_rng(7)
    X = (rng.uniform(size=(2000, 400)) < 0.9).astype(float)
    starts = list(range(0, 400, 20))
    m = AB.fresh_logw(X, 0.9, starts)
    assert np.mean((m >= math.log(20)).any(1)) <= 0.05
    assert np.allclose(m[:, 0], np.log(0.5 * np.exp(AB.agrapa_logw(X[:, :1], 0.9)[:, 0]) + 0.5))   # batch 1 weighs 1/2


def test_a_late_fall_shows_sooner_with_fresh_starts_when_the_window_is_full_of_good_rows():
    rng = np.random.default_rng(3)
    p = np.r_[np.full(200, 0.97), np.full(200, 0.70)]
    X = (rng.uniform(size=(400, 400)) < p).astype(float)
    lw = AB.all_logw(X, 0.85, list(range(0, 400, 20)))
    first = {d: AB.first_alarm(v) for d, v in lw.items()}
    assert set(first) == set(AB.DESIGNS)
    assert all((f >= 200).mean() > 0.95 for f in first.values())   # no design alarms before the fall, almost ever
