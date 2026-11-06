"""`jevbench driftmodel` (#35) set conformal test martingales aside. That verdict is only fair if they were built right:
their calibrator must be a density, and their conformal p-values uniform when nothing changed."""
import numpy as np

from jevbench import driftmodel


def test_the_calibrator_integrates_to_one():
    # its CDF is (x - 1) / ln x, which runs from 0 at x -> 0 (slowly: about 1 / |ln x|) to 1 at x = 1; check the
    # integral from a to 1 in log space, where the integrand is smooth
    for a in (1e-3, 1e-8, 1e-12):
        u = np.linspace(np.log(a), 0, 400_001)
        f = driftmodel.calibrator(np.exp(u)) * np.exp(u)
        integral = float(((f[1:] + f[:-1]) / 2 * np.diff(u)).sum())
        assert abs(integral - (1 - (a - 1) / np.log(a))) < 1e-4


def test_conformal_p_values_are_uniform_for_exchangeable_asks():
    # 2,000 anchors, each asked 8 times by an unchanged model that is right with its own chance (ties everywhere):
    # every new ask's smoothed conformal p-value among the earlier ones must be uniform
    rng = np.random.default_rng(7)
    q = rng.uniform(0.5, 1.0, 2000)
    asks = (rng.random((2000, 8)) > q[:, None]).astype(float)        # 1 = wrong, the stranger the larger
    ps = np.concatenate([driftmodel.conformal_p(asks[:, :k], asks[:, k], rng) for k in range(1, 8)])
    assert abs(ps.mean() - 0.5) < 0.01
    assert all(abs(np.mean(ps <= u) - u) < 0.01 for u in (0.05, 0.25, 0.5, 0.75))


def test_a_rotating_panel_counts_each_row_once():
    # the rotating panel's evidence counts a row only at its first re-ask: 300 anchors, 50 a check, 12 checks
    task = {"right0": np.ones(300, bool), "s0": np.full(300, 0.9),
            "again": [(np.ones(300, bool), np.full(300, 0.9))], "changes": {}}
    st = driftmodel.simulate(task, 50, 12, None, 1.0, 1, 3, np.random.default_rng(1))
    assert st["fresh"].sum(1).tolist() == [300, 300, 300]
    assert st["fresh"][0].tolist() == [50] * 6 + [0] * 6
