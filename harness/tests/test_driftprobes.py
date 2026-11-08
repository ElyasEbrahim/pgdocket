"""`jevbench driftprobes` (#36) compares probes added to the anchors with the same calls spent on anchors."""
import numpy as np

from jevbench import driftprobes


def test_the_designs_hold_the_anchors_or_the_calls_fixed():
    d = driftprobes.designs(50)
    assert d["anchors"] == (50, 0) and d["twice the anchors"] == (100, 0)
    assert d["anchors + as many probes"] == (50, 50) and sum(d["anchors + as many probes"]) == sum(d["twice the anchors"])
    assert d["anchors + 4× as many probes"] == (50, 200)


def test_no_design_alarms_when_nothing_changes_and_answers_never_flip():
    task = {"right0": np.ones(400, bool), "s0": np.full(400, 0.9),
            "again": [(np.ones(400, bool), np.full(400, 0.9))], "changes": {}}
    out = driftprobes.series(task, 100, 25, 12, None, 1.0, 1, 5, np.random.default_rng(1))
    assert set(out) == set(driftprobes.designs(25)) and all((f > 12).all() for f in out.values())
