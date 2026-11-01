import math

import numpy as np
import pytest

from pgdocket import groups, multi
from pgdocket.estimate import norm_ppf

Z = norm_ppf(0.975)
NAN = np.nan


def _population(rng, N, share=0.3, good=0.92, cheap=0.75):
    """N rows with the answer at `share`; a good model right on `good` of them, a cheap one on `cheap`."""
    y = (rng.random(N) < share).astype(float)
    flip = lambda p: np.where(rng.random(N) < p, y, 1 - y)
    return y, flip(good), flip(cheap)


def test_one_model_on_every_row_is_ppi():
    rng = np.random.default_rng(1)
    y, f, _ = _population(rng, 5000)
    lab = np.zeros(5000, bool)
    lab[rng.choice(5000, 300, replace=False)] = True
    e = multi.estimate(multi.pattern_stats(["labels", "v1"], [np.where(lab, y, NAN), f]), Z, 5000)
    p = groups.ppi_binary(300, int(y[lab].sum()), int(f[lab].sum()), int((f * y)[lab].sum()), 4700, int(f[~lab].sum()))
    assert e["theta"] == pytest.approx(p["theta"], abs=0.004)
    assert e["se"] == pytest.approx(p["se"], rel=0.1)
    assert e["used"] == ["labels", "v1"] and e["rows"] == {"labels": 300, "v1": 5000}


def test_labels_inside_a_good_models_sample_inside_a_cheap_models_rows_cover():
    rng = np.random.default_rng(2)
    N = 20000
    y, good, cheap = _population(rng, N)
    hits, widths = [], {"all": [], "labels": []}
    for _ in range(300):
        order = rng.permutation(N)
        lab, sample = np.zeros(N, bool), np.zeros(N, bool)
        lab[order[:200]], sample[order[:3000]] = True, True
        st = multi.pattern_stats(["labels", "good", "cheap"], [np.where(lab, y, NAN), np.where(sample, good, NAN), cheap])
        e = multi.estimate(st, Z, N)
        hits.append(e["ci"][0] <= y.mean() <= e["ci"][1])
        widths["all"].append(e["se"])
        widths["labels"].append(multi.estimate(multi._restrict(st, [0]), Z, N)["se"])
    assert np.mean(hits) >= 0.93
    assert np.mean(widths["all"]) < 0.65 * np.mean(widths["labels"])     # the models narrow it by over a third


def test_a_model_whose_rows_miss_the_labels_is_left_out():
    rng = np.random.default_rng(3)
    y, good, _ = _population(rng, 10000)
    lab, sample = np.zeros(10000, bool), np.zeros(10000, bool)
    lab[:200], sample[5000:8000] = True, True                          # no row has both
    st = multi.pattern_stats(["labels", "v2"], [np.where(lab, y, NAN), np.where(sample, good, NAN)])
    e = multi.estimate(st, Z, 10000)
    assert e["used"] == ["labels"] and e["theta"] == pytest.approx(y[:200].mean())


def test_models_that_always_agree_and_a_perfect_model_still_give_an_interval():
    rng = np.random.default_rng(4)
    y, _, _ = _population(rng, 4000, share=0.06)
    lab = np.zeros(4000, bool)
    lab[:600] = True
    st = multi.pattern_stats(["labels", "a", "b"], [np.where(lab, y, NAN), y.copy(), y.copy()])
    e = multi.estimate(st, Z, 4000)
    assert e["used"] == ["labels", "a"]                                 # b repeats a: it adds nothing
    assert e["theta"] == pytest.approx(y.mean(), abs=0.01) and e["se"] > 0.002   # half a disagreement each way


def test_every_row_labelled_is_exact_and_one_sided_labels_give_nothing():
    y = np.r_[np.ones(30), np.zeros(70)]
    assert multi.estimate(multi.pattern_stats(["labels", "v1"], [y, y]), Z, 100)["exact"]
    lab = np.where(np.arange(100) < 20, 0.0, NAN)                       # 20 labels, none with the answer
    assert multi.estimate(multi.pattern_stats(["labels", "v1"], [lab, y]), Z, 100)["theta"] is None


def test_the_plan_is_double_sampling_with_one_model_and_drops_a_model_not_worth_its_cost():
    share, rho, w0, w1 = 0.3, 0.8, 1.0, 1e-4
    plans = multi.plan(share, [rho], [w1], w0, 10 ** 9, 0.02, Z, ["v1"])
    best = plans[0]
    assert best["kind"] == "models on samples"
    ratio = best["rows"]["v1"] / best["labels"]
    assert ratio == pytest.approx(math.sqrt(w0 * rho ** 2 / (w1 * (1 - rho ** 2))), rel=0.01)
    V = (0.02 / Z) ** 2
    m0 = share * (1 - share) * ((1 - rho ** 2) + rho ** 2 / ratio) / V                # its variance hits the target
    assert best["labels"] == pytest.approx(m0, rel=0.01)
    alone = next(p for p in plans if p["kind"] == "labels alone")
    assert alone["labels"] == math.ceil(share * (1 - share) / V)
    # a second, weaker model is worth having only if it is cheap enough: (ρ1² − ρ2²) / ρ2² = 0.31 of the first's cost
    plans = multi.plan(share, [0.8, 0.7], [1e-4, 5e-4], w0, 10 ** 9, 0.02, Z, ["v1", "v2"])
    assert all(p["models"] != ["v1", "v2"] for p in plans)
    plans = multi.plan(share, [0.8, 0.7], [1e-4, 1e-6], w0, 10 ** 9, 0.02, Z, ["v1", "v2"])
    assert plans[0]["models"] == ["v1", "v2"] and plans[0]["rows"]["v2"] > plans[0]["rows"]["v1"]


def test_the_plan_caps_at_the_table_and_counts_what_is_done():
    plans = multi.plan(0.3, [0.8], [1e-4], 1.0, 5000, 0.02, Z, ["v1"])
    assert plans[0]["rows"]["v1"] == 5000                                # a small table: judge it all
    free = multi.plan(0.3, [0.8], [0.0], 1.0, 10 ** 6, 0.02, Z, ["v1"])[0]
    assert free["rows"]["v1"] == 10 ** 6                                 # a free model judges every row
    done = multi.plan(0.3, [0.8], [1e-4], 1.0, 10 ** 6, 0.02, Z, ["v1"], labelled=400, judged=[10 ** 6])[0]
    assert done["rows"]["v1"] == 10 ** 6 and done["more_rows"]["v1"] == 0 and done["cost"] == done["more_labels"]
