import math
from collections import Counter

import numpy as np
import pytest

from pgdocket import drift
from pgdocket.metrics import ks_2samp, proportion_tests, spent_alpha


def test_proportion_tests_match_the_formula():
    # 30 of 100 against 50 of 100: pooled share 0.4, z = 0.2 / sqrt(0.4 * 0.6 * 0.02) = 2.887, two-sided p = 0.0039
    tests, k = proportion_tests({"a": 30, "b": 70}, {"a": 50, "b": 50})
    assert k == 1 and tests[0][:3] == ("a", 0.3, 0.5) and tests[0][3] == pytest.approx(0.00389, abs=1e-5)
    tests, k = proportion_tests({"x": 300, "y": 300, "rare": 2}, {"x": 40, "y": 60, "rare": 1})
    assert k == 2 and [t[0] for t in tests] == ["x", "y"]               # too rare to test: left out


def test_two_sample_tests_keep_their_size():
    rng = np.random.default_rng(0)
    probs = [0.4, 0.3, 0.15, 0.1, 0.05]
    mix = ks = 0
    for _ in range(2000):
        a, b = rng.choice(5, 300, p=probs), rng.choice(5, 120, p=probs)
        tests, k = proportion_tests(Counter(a), Counter(b))
        mix += min(t[3] for t in tests) * k < 0.05
        ks += ks_2samp(rng.beta(2, 5, 300), rng.beta(2, 5, 120))[1] < 0.05
    assert mix / 2000 <= 0.06                                # Bonferroni: conservative
    assert ks / 2000 <= 0.06                                 # the asymptotic p is slightly conservative


def test_ks_is_conservative_on_tied_scores():
    # Jev's scores sit on a 0.01 grid, so many tie; the test must not alarm more often because of it
    rng = np.random.default_rng(1)
    hits = sum(ks_2samp(np.round(rng.beta(5, 2, 400), 2), np.round(rng.beta(5, 2, 150), 2))[1] < 0.05 for _ in range(2000))
    assert hits / 2000 <= 0.05


def test_spent_alpha_sums_to_alpha():
    assert spent_alpha(0.05, 1) == pytest.approx(0.05 * 6 / math.pi ** 2)
    assert sum(spent_alpha(0.05, k) for k in range(1, 100_000)) < 0.05


def test_repeated_data_checks_rarely_alarm_without_a_shift():
    # 1,000 decisions, each checked every week for 20 weeks on rows from one unchanging population: the chance of
    # any alarm over all 20 checks must stay ≤ 5 % (1,000 trials leave about ±1.4 points of simulation noise)
    rng = np.random.default_rng(2)
    probs, any_alarm = [0.5, 0.3, 0.2], 0
    for _ in range(1000):
        for k in range(1, 21):
            ref, new = rng.choice(3, 400, p=probs), rng.choice(3, 60, p=probs)
            r = drift.windows(list(ref), list(rng.beta(4, 2, 400)), list(new), list(rng.beta(4, 2, 60)), spent_alpha(0.05, k))
            if r["mix"]["alarm"] or r["scores"]["alarm"]:
                any_alarm += 1
                break
    assert any_alarm / 1000 <= 0.06


def test_a_data_shift_is_caught():
    rng = np.random.default_rng(3)
    caught = 0
    for _ in range(200):
        ref = rng.choice(["bug", "feature", "question"], 1000, p=[0.5, 0.3, 0.2])
        new = rng.choice(["bug", "feature", "question"], 200, p=[0.35, 0.45, 0.2])        # features become common
        r = drift.windows(list(ref), list(rng.beta(4, 2, 1000)), list(new), list(rng.beta(3, 2, 200)), spent_alpha(0.05, 1))
        caught += r["mix"]["alarm"]
        assert r["mix"]["moved"][0]["answer"] in ("bug", "feature")
    assert caught / 200 >= 0.9


def test_a_tiny_shift_on_many_rows_is_not_an_alarm():
    # significant, but too small to matter: 1 point of the answer mix on 200,000 rows
    rng = np.random.default_rng(4)
    ref, new = rng.choice(2, 200_000, p=[0.70, 0.30]), rng.choice(2, 200_000, p=[0.69, 0.31])
    r = drift.windows(list(ref), list(rng.beta(4, 2, 2000)), list(new), list(rng.beta(4, 2, 2000)), 0.05)
    assert r["mix"]["p"] < 0.01 and not r["mix"]["alarm"]


def _anchor_run(rng, acc_old, acc_new, n=150):
    """Each row has its own chance of being answered right; the stored answer and the re-ask are two draws."""
    ease = rng.beta(4, 1.5, n)
    orig = rng.uniform(size=n) < np.clip(ease * acc_old / ease.mean(), 0, 1)
    new = rng.uniform(size=n) < np.clip(ease * acc_new / ease.mean(), 0, 1)
    s_old = np.round(np.clip(ease + rng.normal(0, 0.05, n), 0, 1), 2)
    s_new = np.round(np.clip(ease + rng.normal(0, 0.05, n), 0, 1), 2)
    return orig, new, s_old, s_new


def test_repeated_model_checks_rarely_alarm_without_a_change():
    rng = np.random.default_rng(5)
    any_alarm = 0
    for _ in range(1000):
        orig, _, s_old, _ = _anchor_run(rng, 0.8, 0.8)
        for k in range(1, 13):                                  # the same panel of anchors, re-asked monthly for a year
            _, new, _, s_new = _anchor_run(rng, 0.8, 0.8)
            r = drift.anchors(orig, new, s_old, s_new, spent_alpha(0.05, k))
            if r["accuracy"]["alarm"] or r["scores"]["alarm"]:
                any_alarm += 1
                break
    assert any_alarm / 1000 <= 0.06


def test_a_worse_model_is_caught():
    rng = np.random.default_rng(6)
    # these simulated re-asks disagree far more often than Jev's do (1-5 % of rows), so real power is higher
    caught = sum(drift.anchors(*_anchor_run(rng, 0.85, 0.65, n=200), spent_alpha(0.05, 1))["accuracy"]["alarm"]
                 for _ in range(200))
    assert caught / 200 >= 0.95


def test_one_sided_sign_test_is_exact():
    assert drift.sign_test_one_sided(8, 0) == pytest.approx(1 / 256)
    assert drift.sign_test_one_sided(0, 8) == 1.0
    assert drift.sign_test_one_sided(0, 0) == 1.0
    assert drift.sign_test_one_sided(6, 4) == pytest.approx(sum(math.comb(10, i) for i in range(6, 11)) / 1024)
