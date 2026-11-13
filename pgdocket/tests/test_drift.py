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


# ---------------------------------------------------------------- anytime-valid rows (#33)
def test_evidence_read_every_day_rarely_reaches_its_threshold_without_a_change():
    """1,000 streams from one unchanging population, the e-value read after every one of 40 days: the chance it ever
    reaches 1/α stays at most α (here it is far below: universal inference is conservative)."""
    rng = np.random.default_rng(33)
    ever = 0
    for _ in range(1000):
        p = rng.uniform(0.05, 0.6)
        k_ref, m = int(rng.binomial(400, p)), 400
        ks, ns = [], []
        for _ in range(40):
            ns.append(int(rng.integers(5, 40)))
            ks.append(int(rng.binomial(ns[-1], p)))
            if drift.log_evidence(k_ref, m, ks, ns) >= math.log(1 / 0.05):
                ever += 1
                break
    assert ever / 1000 <= 0.05


def test_evidence_piles_up_for_a_shift_that_lasts():
    """A share that moves from 30 % to 45 % on day 11, 30 rows a day: no single day's rows show it reliably, but the
    evidence since the cut does within three weeks, most of the time; before day 11 it stays low."""
    rng = np.random.default_rng(34)
    caught, early = 0, 0
    for _ in range(200):
        k_ref, m = int(rng.binomial(1000, 0.3)), 1000
        ks, ns = [], []
        for d in range(1, 32):
            ns.append(30)
            ks.append(int(rng.binomial(30, 0.3 if d < 11 else 0.45)))
            e = drift.log_evidence(k_ref, m, ks, ns)
            if d == 10:
                early += e >= math.log(20)
        caught += e >= math.log(20)
    assert caught / 200 >= 0.8 and early / 200 <= 0.05


def test_the_rows_check_keeps_alarming_after_a_shift_has_lasted():
    """Weekly checks of 60 rows; from week 2 a third answer makes up half the rows. The old window test compared each
    week with everything before it, so once the shifted weeks outnumbered the reference its alarms faded; against the
    rows from before the series began, and with the evidence piling up, they don't. (The first weeks of 60 rows,
    against 120, aren't always enough: with the evidence on a fifth of the budget since #34, its log e-value reached
    6.37 at week 4, for a threshold of 6.40. From week 5 on the shift has lasted, and every check alarms.)"""
    rng = np.random.default_rng(35)
    p0, p1 = [0.5, 0.3, 0.2], [0.3, 0.2, 0.5]
    ref = list(rng.choice(["a", "b", "c"], 120, p=p0))
    ref_s = list(np.round(rng.beta(4, 2, 120), 2))
    new, new_s, day = [], [], []
    alarms, old = [], []
    for week in range(1, 13):
        n = 60
        new += list(rng.choice(["a", "b", "c"], n, p=p0 if week < 2 else p1))
        new_s += list(np.round(rng.beta(4, 2, n), 2))
        day += [7 * (week - 1) + 1] * n
        recent = [d == 7 * (week - 1) + 1 for d in day]
        r = drift.rows_check(ref, ref_s, new, new_s, day, recent, week)
        alarms.append(r["mix"]["alarm"])
        before = ref + new[:-n]                                 # the old reference: everything before the window
        old.append(drift.windows(before, ref_s + new_s[:-n], new[-n:], new_s[-n:], spent_alpha(0.05, week))["mix"]["alarm"])
    assert all(alarms[4:]) and r["mix"]["evidence"]["item"] in ("a", "c") and r["mix"]["evidence"]["alarm"]
    assert sum(old[-6:]) <= 1                                   # the old check had stopped seeing it


def test_two_answers_are_one_test_and_scores_are_tested_at_the_reference_deciles():
    rng = np.random.default_rng(36)
    ref, new = list(rng.choice(["no", "yes"], 300, p=[0.7, 0.3])), list(rng.choice(["no", "yes"], 100, p=[0.4, 0.6]))
    ev = drift.evidence(ref, new, [1] * 100, [True] * 100, "answers", 0.0125, drift.MIN_SHIFT)
    assert ev["tests"] == 1 and ev["item"] == "yes" and ev["alarm"]
    scores = drift.evidence(list(rng.beta(4, 2, 300)), list(rng.beta(2, 4, 100)), [1] * 100, [True] * 100, "scores",
                            0.0125, drift.MIN_KS)
    assert scores["tests"] == 9 and scores["alarm"] and scores["after"] > scores["before"]


def _first_reasks(rng, n, q, turn=0.0):
    """n labelled rows asked again for the first time: per row a chance q of a right answer, the stored answer and the
    re-ask two draws from it (a re-ask that was right turns wrong with chance `turn`), and scores on a 0.01 grid."""
    stored = rng.random(n) < q
    new = (rng.random(n) < q) & ~(rng.random(n) < turn)
    s0, s1 = np.round(rng.beta(8, 2, n), 2), np.round(rng.beta(8, 2, n), 2)
    worse, better = int((stored & ~new).sum()), int((~stored & new).sum())
    return worse, better, int((s1 > s0).sum()), int((s1 < s0).sum())


def test_model_evidence_read_after_every_check_rarely_alarms_without_a_change():
    """#35: 1,000 series of 20 checks, 50 rows asked for the first time at each, nothing changed. Each row's stored
    answer and re-ask are exchangeable, so the evidence, read after every check, alarms at most (1 - MODEL_SHARE) *
    ALPHA of the time (far less: the mixture of bets is cautious)."""
    rng = np.random.default_rng(351)
    alpha = (1 - drift.MODEL_SHARE) * drift.ALPHA
    ever = 0
    for _ in range(1000):
        tot = np.zeros(4, int)
        for _ in range(20):
            tot += _first_reasks(rng, 50, q=rng.uniform(0.6, 0.98))
            ev = drift.model_evidence(*tot, alpha)
            if ev["accuracy"]["alarm"] or ev["scores"]["alarm"]:
                ever += 1
                break
    assert ever / 1000 <= alpha + 0.01


def test_model_evidence_piles_up_where_one_check_cannot_see_a_change():
    """A tenth of right answers turn wrong: a single check of 50 rows rarely shows it, twenty checks of fresh rows do
    (ten caught it in about two runs of three)."""
    rng = np.random.default_rng(352)
    alpha = (1 - drift.MODEL_SHARE) * drift.ALPHA
    one = caught = 0
    for _ in range(200):
        tot = np.zeros(4, int)
        for k in range(1, 21):
            c = _first_reasks(rng, 50, q=0.9, turn=0.1)
            one += k == 1 and drift.sign_test_one_sided(c[0], c[1]) < spent_alpha(drift.MODEL_SHARE * drift.ALPHA, 1) / 2
            tot += c
        caught += drift.model_evidence(*tot, alpha)["accuracy"]["alarm"]
    assert one / 200 <= 0.35 and caught / 200 >= 0.9


def test_log_bets_is_a_mixture_of_fair_coin_bets():
    assert drift.log_bets(0, 0) == 0.0 and drift.log_bets(0, 0, two_sided=True) == 0.0
    assert drift.log_bets(5, 0) > drift.log_bets(4, 0) > drift.log_bets(4, 1)
    assert abs(drift.log_bets(7, 2, two_sided=True) - drift.log_bets(2, 7, two_sided=True)) < 1e-12
    lams = drift.BETS
    assert abs(drift.log_bets(6, 3) - math.log(np.mean((1 + lams) ** 6 * (1 - lams) ** 3))) < 1e-12
    # each bet is a fair game: its expected factor on one fair coin is 1
    assert np.allclose(0.5 * (1 + lams) + 0.5 * (1 - lams), 1.0)


def test_rows_without_labels_count_towards_scores_but_never_accuracy():
    """#36: 4 labelled anchors and 6 rows without labels asked again together. Accuracy is the anchors'; the scores test
    and the scores evidence take all ten rows."""
    o_right, n_right = [True, True, False, True], [False, True, True, True]
    o_scores = [0.9, 0.8, 0.7, 0.9] + [0.6] * 6
    n_scores = [0.8, 0.8, 0.9, 0.9] + [0.5] * 6
    r = drift.anchors(o_right, n_right, o_scores, n_scores, 0.05)
    assert r["anchors"] == 4 and (r["accuracy"]["worse"], r["accuracy"]["better"]) == (1, 1)
    assert (r["scores"]["up"], r["scores"]["down"]) == (1, 7)
    assert drift.signs(o_right, n_right, o_scores, n_scores) == (1, 1, 1, 7)


def test_the_model_verdict_names_rows_without_labels_and_says_when_the_labels_ran_out():
    r = drift.model_rows([True] * 20, [True] * 20, [0.9] * 120, [0.9] * 120, 3, (0, 0, 0, 0))
    r |= {"run": 3, "days_apart": 0.0, "probes": 100, "evidence_rows": 20, "evidence_rows_scores": 120,
          "labelled_left": 0, "series_began": "2026-11-08T10:00:00+00:00"}
    text = "\n".join(drift.verdict_model(r))
    assert "20 labelled rows and 100 without labels asked again" in text
    assert "evidence from 20 first re-asks since 2026-11-08" in text and "evidence from 120 first re-asks" in text
    assert "every labelled row has been asked in this series" in text and "--probes M" not in text
    r["probes"] = 0
    assert "--probes M keeps the scores evidence growing" in "\n".join(drift.verdict_model(r))


def test_the_odd_one_out_bet_is_fair_without_a_change():
    """#37, #38: without a change the stored answer is the odd one out of three exchangeable answers with chance 1/3, and
    the mixture of bets on it is then a test martingale: its expected value is 1 whatever the number of rows."""
    for n in (1, 5, 30):
        pmf = [math.comb(n, k) * (1 / 3) ** k * (2 / 3) ** (n - k) for k in range(n + 1)]
        assert sum(p * math.exp(drift.log_odd(k, n)) for k, p in enumerate(pmf)) == pytest.approx(1.0, rel=1e-9)
    assert drift.log_odd(0, 0) == 0 and drift.log_odd(12, 36) < 0 < drift.log_odd(25, 36)
    assert drift.log_odd(9, 12, chance=0.5) == pytest.approx(drift.log_bets(9, 3))   # one chance in two: the sign bets
    assert drift.log_odd(np.array([[3, 30]]), np.array([[9, 36]])).shape == (1, 2)


def test_three_draws_of_one_model_put_the_stored_answer_odd_a_third_of_the_time():
    rng = np.random.default_rng(38)
    probs = rng.dirichlet(np.ones(4), 20000)                  # each row its own answer probabilities
    draws = np.array([[rng.choice(4, p=p) for _ in range(3)] for p in probs[:6000]])
    o = drift.odd_one(*draws.T)
    assert set(np.unique(o)) <= {-1, 0, 1, 2}
    share = (o == 0).sum() / (o >= 0).sum()
    assert abs(share - 1 / 3) < 0.02                         # thousands of rows with an odd one out
    assert drift.odd_one(["a", "a", "b", "c"], ["b", "a", "b", "d"], ["b", "a", "a", "e"]).tolist() == [0, -1, 2, -1]


def test_the_label_free_half_of_the_evidence_is_split_with_rows_asked_twice():
    ev = drift.model_evidence(0, 0, 0, 0, (1 - drift.MODEL_SHARE) * drift.ALPHA)
    needs = {part: math.exp(ev[part]["threshold"]) for part in ("accuracy", "scores", "answers")}
    assert needs["accuracy"] == pytest.approx(133.3, abs=0.1)                       # unchanged since #35
    assert needs["scores"] == pytest.approx(266.7, abs=0.1)                         # since #38
    assert needs["answers"] == pytest.approx(533.3, abs=0.1)                        # #39: the pairs' share, split in two
    assert math.exp(ev["twice_scores"]["threshold"]) == pytest.approx(533.3, abs=0.1)
    hit = drift.model_evidence(0, 0, 0, 0, 0.015, odd=(30, 32))
    assert hit["answers"]["alarm"] and not hit["scores"]["alarm"] and not hit["accuracy"]["alarm"]
    assert not hit["twice_scores"]["alarm"]
    out = drift.model_rows([True] * 20, [True] * 20, [0.9] * 20, [0.9] * 20, 1, (0, 0, 0, 0), (30, 32))
    assert out["answers"]["alarm"] and not out["accuracy"]["alarm"] and not out["scores"]["alarm"]
    out = drift.model_rows([True] * 20, [True] * 20, [0.9] * 20, [0.9] * 20, 1, (0, 0, 0, 0), (0, 0), (30, 32))
    assert out["twice_scores"]["alarm"] and not out["answers"]["alarm"]


def test_the_verdict_names_rows_asked_twice():
    r = {"run": 2, "days_apart": 3.0, "probes": 40, "evidence_rows": 20, "evidence_rows_scores": 60, "labelled_left": 5,
         "asked_twice": 40, "series_began": "2026-11-13T00:00:00+00:00"} | drift.model_rows(
        [True] * 20, [True] * 20, [0.9] * 60, [0.9] * 60, 2, (0, 0, 0, 0), (30, 32))
    lines = drift.verdict_model(r)
    assert any("answers: of 40 rows asked twice since 2026-11-13, 32 had an odd one out, the stored answer on 30" in x
               and "(needs 533)" in x and "⚠ changed" in x for x in lines)
    assert any(x.startswith("  their scores: 0 had an odd one out, the stored score on 0") and "⚠" not in x for x in lines)
    assert lines[-1].startswith("  → the model now gives some rows another answer")
    quiet = drift.verdict_model(r | {"asked_twice": 0})
    assert not any("asked twice" in x or "their scores" in x for x in quiet)   # without --twice the lines stay out of the way
    scored = {**r, **drift.model_rows([True] * 20, [True] * 20, [0.9] * 60, [0.9] * 60, 2, (0, 0, 0, 0), (2, 6), (30, 32))}
    lines = drift.verdict_model(scored)
    assert any(x.startswith("  their scores: 32 had an odd one out, the stored score on 30") and "⚠ changed" in x
               for x in lines)
    assert lines[-1].startswith("  → the model now scores some rows differently than it did, in no one direction")


def test_the_odd_score_is_the_one_the_two_closest_leave_out():
    """#39: by scores, the two closest of three make the pair; the third is the odd one; ties leave none."""
    got = drift.odd_score([0.9, 0.5, 0.90, 0.2, 0.4, 0.7], [0.9, 0.8, 0.90, 0.5, 0.6, 0.7], [0.9, 0.8, 0.70, 0.9, 0.8, 0.7])
    assert got.tolist() == [-1, 0, 2, 2, -1, -1]
    rng = np.random.default_rng(39)
    s = np.round(rng.beta(5, 2, (3, 30000)), 2)               # three draws of one model, on Jev's 0.01 grid
    o = drift.odd_score(*s)
    assert abs((o == 0).sum() / (o >= 0).sum() - 1 / 3) < 0.01
