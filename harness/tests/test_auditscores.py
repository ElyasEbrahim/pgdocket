"""`jevbench auditscores` (#41): how many audit labels the model's scores could save, at best."""
import numpy as np
import pytest

from jevbench.auditscores import ceilings


def test_flat_scores_save_nothing_and_telling_scores_save_a_lot():
    flat = ceilings(np.full(500, 0.9), np.r_[np.ones(450), np.zeros(50)])
    assert flat["labels"] == 1.0 and flat["model_scores"] == pytest.approx(1.0) and flat["share_at_top"] == 1.0
    p = np.r_[np.full(400, 0.99), np.full(100, 0.5)]                 # the score tells the risky rows apart
    rng = np.random.default_rng(41)
    y = rng.uniform(size=500) < p
    told = ceilings(p, y)
    assert told["model_scores"] > 1.3 and told["labels"] > 1.2 and told["model_tilted"] > told["model_scores"]


def test_the_bound_is_one_over_one_minus_the_squared_correlation():
    p = np.array([0.6, 0.7, 0.8, 0.9, 0.95, 0.99] * 50)
    y = np.array([0, 1, 1, 1, 1, 1] * 50, float)
    r = np.corrcoef(p, y)[0, 1]
    assert ceilings(p, y)["labels"] == pytest.approx(1 / (1 - r * r))
