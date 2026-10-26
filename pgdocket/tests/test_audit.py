from datetime import datetime, timezone

import numpy as np

from pgdocket import audit
from pgdocket.metrics import eprocess_below


def test_no_evidence_without_errors():
    assert np.all(eprocess_below(np.ones(200), 0.9) == 1.0)              # every audited row right: no bet is placed


def test_alarms_stay_rare_however_often_you_look():
    # precision exactly at the target is the hardest case; the wealth is read after every one of 600 rows
    rng = np.random.default_rng(0)
    alarms = sum((eprocess_below(rng.uniform(size=600) < 0.9, 0.9) >= 20).any() for _ in range(1000))
    assert alarms / 1000 <= 0.05
    # precision that changes from batch to batch but never falls below the target
    alarms = sum((eprocess_below(rng.uniform(size=600) < np.repeat(rng.uniform(0.9, 1.0, 30), 20), 0.9) >= 20).any()
                 for _ in range(1000))
    assert alarms / 1000 <= 0.05


def test_a_fall_below_target_is_caught():
    rng = np.random.default_rng(1)
    first = []
    for _ in range(300):
        hit = np.flatnonzero(eprocess_below(rng.uniform(size=400) < 0.75, 0.9) >= 20)
        first.append(hit[0] + 1 if len(hit) else np.inf)
    assert np.mean(np.isfinite(first)) >= 0.99 and np.median(first) <= 70


def test_labels_to_detect_is_close_to_what_the_test_needs():
    rng = np.random.default_rng(2)                       # an independent simulation of the same fall
    first = []
    for _ in range(300):
        hit = np.flatnonzero(eprocess_below(rng.uniform(size=2000) < 0.8, 0.9) >= 20)
        first.append(hit[0] + 1)
    assert 0.8 <= np.median(first) / audit.labels_to_detect(0.9, 0.1) <= 1.25


def _rows(batches):
    t = datetime(2026, 10, 26, tzinfo=timezone.utc)
    return [{"batch": b, "drawn_at": t, "labelled": r is not None, "right": r} for b, rs in enumerate(batches, 1) for r in rs]


def test_report_alarms_and_names_the_batch():
    ok = audit.analyse(_rows([[True] * 19 + [False], [True] * 18 + [False, None]]), 0.9)
    assert not ok["alarm"] and ok["labelled"] == 39 and ok["right"] == 37
    text = "\n".join(audit.render("triage", {"version": 1, "calibration": {"fitted_at": "2026-10-26T10:00:00"}}, ok))
    assert "no sign that the auto-accepted answers fell below 90.0 %" in text and "left out" in text
    bad = audit.analyse(_rows([[True] * 18 + [False] * 2, [True] * 12 + [False] * 8, [True] * 12 + [False] * 8]), 0.9)
    assert bad["alarm"] and bad["alarm_batch"] in (2, 3)
    text = "\n".join(audit.render("triage", {"version": 1, "calibration": {"fitted_at": "2026-10-26T10:00:00"}}, bad))
    assert "⚠ the auto-accepted answers are below the 90.0 % target" in text
