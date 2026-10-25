import numpy as np
import pytest

from pgdocket.metrics import clopper_pearson_lower, precision_threshold


def test_clopper_pearson_solves_its_definition():
    # The one-sided lower bound L satisfies P(X >= k | n, L) = alpha; check with exact integer arithmetic.
    from math import comb

    def tail(k, n, p):
        return sum(comb(n, j) * p ** j * (1 - p) ** (n - j) for j in range(k, n + 1))

    assert clopper_pearson_lower(0, 10) == 0.0
    assert clopper_pearson_lower(10, 10) == pytest.approx(0.05 ** (1 / 10), abs=1e-9)
    for k, n in [(50, 100), (95, 100), (7, 30), (290, 300)]:
        assert tail(k, n, clopper_pearson_lower(k, n)) == pytest.approx(0.05, abs=1e-6)


def test_clopper_pearson_coverage():
    rng = np.random.default_rng(0)
    p, n, misses = 0.85, 60, 0
    for _ in range(2000):
        k = rng.binomial(n, p)
        misses += clopper_pearson_lower(k, n) > p
    assert misses / 2000 <= 0.06                 # nominal 5 %, conservative


def test_precision_threshold_holds_its_guarantee():
    rng = np.random.default_rng(1)
    fails, chosen = 0, 0
    for _ in range(300):
        score = rng.uniform(0, 1, 800)
        correct = rng.uniform(0, 1, 800) < score            # calibrated: precision rises with the threshold
        res = precision_threshold(score, correct, target=0.85)
        if res["chosen"]:
            chosen += 1
            t = res["chosen"]["threshold"]
            true_precision = (1 + t) / 2                     # E[score | score >= t] for uniform scores
            fails += true_precision < 0.85
    assert chosen > 250
    assert fails / 300 <= 0.05


def test_precision_threshold_refuses_when_target_is_unreachable():
    rng = np.random.default_rng(2)
    score = rng.uniform(0, 1, 500)
    correct = rng.uniform(0, 1, 500) < 0.5                   # scores carry no information
    assert precision_threshold(score, correct, target=0.9)["chosen"] is None


def test_precision_threshold_starts_where_the_target_is_provable():
    # 500 rows, top 30 % perfectly right: a start at 25 rows could never certify 90 %, which used to end the search.
    rng = np.random.default_rng(5)
    score = rng.uniform(0, 1, 500)
    correct = np.where(score > 0.7, True, rng.uniform(0, 1, 500) < 0.5)
    res = precision_threshold(score, correct, target=0.9)
    assert res["min_accept"] >= 58
    assert res["chosen"] and res["chosen"]["coverage"] >= 0.2


def test_the_threshold_holds_where_the_view_applies_it():
    """End to end: fit on a random sample of labels, mark `auto` on every row as the view does, and measure the true
    precision of the auto rows."""
    from pgdocket import calibration
    opts = ["a", "b", "c"]
    spec = {"kind": "choice", "question": "q", "options": [{"name": o} for o in opts]}
    rng = np.random.default_rng(12)
    trials, misses, found = 200, 0, 0
    for _ in range(trials):
        s = np.round(rng.uniform(0.34, 1.0, 2000), 2)                   # Jev-like 0.01 grid, with exact 1.0s
        pick = rng.integers(0, 3, 2000)
        right = rng.random(2000) < 0.25 + 0.75 * s                      # precision crosses 85 % mid-range
        truth = np.where(right, pick, (pick + rng.integers(1, 3, 2000)) % 3)
        answers = [{"choice": opts[p], "probabilities": {o: (x if i == p else (1 - x) / 2) for i, o in enumerate(opts)}}
                   for p, x in zip(pick, s)]
        lab = rng.choice(2000, 200, replace=False)
        cal = calibration.fit(spec, [answers[i] for i in lab], [opts[truth[i]] for i in lab], precision_target=0.85)
        # the view accepts exactly the labelled rows the threshold was tested on (it once applied another scale)
        assert np.mean([calibration.auto(answers[i], spec, cal) for i in lab]) == pytest.approx(cal["coverage"])
        if cal["threshold"] is None:
            continue
        found += 1
        auto = np.array([calibration.auto(a, spec, cal) for a in answers])
        misses += (pick[auto] == truth[auto]).mean() < 0.85
    assert found > trials / 2
    assert misses / found <= 0.07                                       # stated: at most 5 %


def test_per_answer_flags_only_answers_demonstrably_below_target():
    from pgdocket.metrics import per_answer
    score = np.ones(43)
    answer = np.array(["a"] * 30 + ["b"] * 10 + ["c"] * 3)
    correct = np.array([1.0] * 30 + [1, 1, 1] + [0] * 7 + [1, 1, 0])
    rows = {r["answer"]: r for r in per_answer(score, correct, answer, 0.5, target=0.9)}
    assert rows["a"]["auto"] == 30 and rows["a"]["precision"] == 1.0 and not rows["a"]["below_target"]
    assert rows["b"]["precision"] == 0.3 and rows["b"]["upper"] < 0.9 and rows["b"]["below_target"]
    assert rows["c"]["upper"] > 0.9 and not rows["c"]["below_target"]       # 2 of 3: too few rows to say
    assert [r["answer"] for r in per_answer(score, correct, answer, 0.5)] == ["a", "b", "c"]     # largest first


def _three_answers(rng, n):
    """A good answer, a middling one and a bad one; precision rises with the score within each."""
    opts = ["good", "mid", "bad"]
    base = {"good": 0.93, "mid": 0.6, "bad": 0.1}
    s = np.round(rng.uniform(0.34, 1.0, n), 2)
    pick = rng.choice(3, n, p=[0.6, 0.25, 0.15])
    right = rng.random(n) < np.array([base[opts[p]] for p in pick]) + (1 - np.array([base[opts[p]] for p in pick])) * s ** 4
    truth = np.where(right, pick, (pick + rng.integers(1, 3, n)) % 3)
    answers = [{"choice": opts[p], "probabilities": {o: (x if i == p else (1 - x) / 2) for i, o in enumerate(opts)}}
               for p, x in zip(pick, s)]
    return opts, pick, truth, answers


def test_a_guarantee_per_answer_holds_for_each_answer():
    """--per-answer: for each answer that gets a threshold, its accepted rows reach the target (95 % confidence)."""
    from pgdocket import calibration
    rng = np.random.default_rng(21)
    spec = {"kind": "choice", "question": "q", "options": [{"name": o} for o in ("good", "mid", "bad")]}
    trials, misses, certified = 150, {"good": 0, "mid": 0, "bad": 0}, {"good": 0, "mid": 0, "bad": 0}
    for _ in range(trials):
        opts, pick, truth, answers = _three_answers(rng, 3000)
        lab = rng.choice(3000, 400, replace=False)
        labels = [opts[truth[i]] for i in lab]
        cal = calibration.fit(spec, [answers[i] for i in lab], labels, precision_target=0.9, per_answer=True)
        assert np.mean([calibration.auto(answers[i], spec, cal) for i in lab]) == pytest.approx(cal["coverage"])
        auto = np.array([calibration.auto(a, spec, cal) for a in answers])
        for k, o in enumerate(opts):
            m = auto & (pick == k)
            if cal["thresholds"].get(o) is not None and m.any():
                certified[o] += 1
                misses[o] += (truth[m] == k).mean() < 0.9
    assert certified["good"] > trials * 0.6
    for o in opts:
        assert misses[o] <= max(2, 0.07 * certified[o]), (o, misses[o], certified[o])


def test_calibrate_reports_an_answer_that_is_confidently_wrong():
    """Overall mode: the guarantee holds for all accepted rows, and the weak answer is named in `per_answer`."""
    from pgdocket import calibration
    rng = np.random.default_rng(22)
    opts = ["a", "b", "c"]
    spec = {"kind": "choice", "question": "q", "options": [{"name": o} for o in opts]}
    pick = rng.choice(3, 400, p=[0.8, 0.1, 0.1])
    right = rng.random(400) < np.where(pick == 2, 0.3, 0.97)              # c is wrong 70 % of the time, at 0.95
    truth = np.where(right, pick, (pick + 1) % 3)
    answers = [{"choice": opts[p], "probabilities": {o: (0.95 if i == p else 0.025) for i, o in enumerate(opts)}}
               for p in pick]
    cal = calibration.fit(spec, answers, [opts[t] for t in truth], precision_target=0.8)
    assert cal["threshold"] is not None and cal["observed_precision"] >= 0.8
    assert [r["answer"] for r in cal["per_answer"] if r["below_target"]] == ["c"]
    assert cal["per_answer_coverage"] < cal["coverage"]                    # its own guarantee would accept less


def test_recall_threshold_holds_its_guarantee():
    """Rows below the threshold are answered "no": the true recall stays >= target in >= 95 % of trials (#21)."""
    from pgdocket.metrics import recall_threshold
    rng = np.random.default_rng(41)
    trials, fails, found, dismissed = 400, 0, 0, []
    for _ in range(trials):
        y = rng.random(5000) < 0.25
        s = np.round(np.clip(np.where(y, rng.beta(5, 2, 5000), rng.beta(2, 5, 5000)), 0, 1), 2)   # Jev-like 0.01 grid
        lab = rng.choice(5000, 300, replace=False)
        c = recall_threshold(s[lab], y[lab], target=0.9)["chosen"]
        if c is None:
            continue
        found += 1
        fails += (s[y] >= c["threshold"]).mean() < 0.9
        dismissed.append((s < c["threshold"]).mean())
    assert found > trials * 0.9 and np.mean(dismissed) > 0.3      # it does let a large share be answered "no"
    assert fails / found <= 0.07                                   # stated: at most 5 %


def test_recall_target_needs_enough_labelled_yeses():
    from pgdocket.metrics import recall_threshold
    s, y = np.linspace(0, 1, 200), np.r_[np.zeros(171, bool), np.ones(29, bool)]   # 29 yeses, all scored highest
    r = recall_threshold(s, y, target=0.9)
    assert r["positives_needed"] == 29 and r["chosen"] is not None and r["chosen"]["recall"] == 1.0
    assert recall_threshold(s[1:], y[1:] & (np.arange(199) > 0), target=0.9)["chosen"] is not None
    assert recall_threshold(s, np.r_[np.zeros(172, bool), np.ones(28, bool)], target=0.9)["chosen"] is None


def test_three_bands_for_a_yes_no_decision():
    from pgdocket import calibration
    rng = np.random.default_rng(42)
    spec = {"kind": "noul", "question": "q"}
    y = rng.random(400) < 0.3
    p = np.round(np.clip(np.where(y, rng.beta(6, 2, 400), rng.beta(2, 6, 400)), 0.01, 0.99), 2)
    answers = [{"noul": float(x)} for x in p]
    cal = calibration.fit(spec, answers, list(y), precision_target=0.9, recall_target=0.9)
    t, r = cal["threshold"], cal["recall_threshold"]
    assert t is not None and r is not None and r < t
    for a, x in zip(answers, p):
        band = "yes" if x >= t else "no" if x < r else "review"
        assert calibration.auto(a, spec, cal) == (band != "review")
        if band != "review":
            assert calibration.noul_answer(a, spec, cal) == band
    assert cal["positives"] == int(y.sum()) and 0 < cal["dismissed"] < 1
    with pytest.raises(ValueError, match="yes/no decisions"):
        calibration.fit({"kind": "choice", "question": "q", "options": [{"name": "a"}, {"name": "b"}]},
                        [{"choice": "a", "probabilities": {"a": 0.9, "b": 0.1}}] * 40, ["a"] * 40, recall_target=0.9)
