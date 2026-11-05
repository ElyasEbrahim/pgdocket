import math

import numpy as np
import pytest

from pgdocket import groups, multiweight
from pgdocket.estimate import norm_ppf

Z = norm_ppf(0.975)
NAN = np.nan


def _population(rng, N=4000):
    """Rows with heavy-tailed weights; the answer is commoner among heavy rows; V right on 92 % of rows, the active
    version on 75 % and saying yes to 15 % more of the noes."""
    w = np.floor(rng.pareto(1.1, N) * 3) + 1
    y = (rng.random(N) < np.where(w > 20, 0.45, 0.25)).astype(float)
    good = np.where(rng.random(N) < 0.92, y, 1 - y)
    cheap = np.where(rng.random(N) < 0.75, y, 1 - y)
    cheap = np.where((y == 0) & (rng.random(N) < 0.15), 1.0, cheap)
    return w, y, good, cheap


def _draw(rng, w, n0, m1):
    """Labels on the first n0 rows of a ranking by u / w, V on the first m1."""
    u = rng.random(len(w))
    order = np.argsort(u / w)
    lab, run = np.zeros(len(w), bool), np.zeros(len(w), bool)
    lab[order[:n0]], run[order[:m1]] = True, True
    return u, lab, run


def test_two_phase_by_weight_holds_and_is_narrower_than_the_active_version_alone():
    """Labels inside V's run of the ranking by weight, the active version on every row (#29): the interval holds, and
    is far narrower than #26's estimate from the same labels and the active version alone."""
    rng = np.random.default_rng(29)
    w, y, good, cheap = _population(rng)
    truth = float((w * y).sum() / w.sum())
    hits, widths, widths26 = [], [], []
    for _ in range(300):
        u, lab, run = _draw(rng, w, 150, 1200)
        s = multiweight.sums("all", w, cheap, np.where(run, good, NAN), np.where(lab, y, NAN), u)
        e = multiweight.estimate(s, Z)
        assert e["reliable"] and e["used"] == ["labels", "v1", "v2"]
        hits.append(e["ci"][0] <= truth <= e["ci"][1])
        widths.append(e["se"])
        g = groups.summarise([groups.sums(groups.ALL, w, cheap, y, lab, lab, u)], Z)[0]
        widths26.append(g["est"]["se"])
    assert np.mean(hits) >= 0.93
    assert np.mean(widths) < 0.65 * np.mean(widths26)


def test_without_v_it_is_the_weighted_estimate_of_26():
    rng = np.random.default_rng(30)
    w, y, _, cheap = _population(rng)
    u, lab, _ = _draw(rng, w, 300, 0)
    e = multiweight.estimate(multiweight.sums("all", w, cheap, np.full(len(w), NAN), np.where(lab, y, NAN), u), Z)
    g = groups.summarise([groups.sums(groups.ALL, w, cheap, y, lab, lab, u)], Z)[0]
    assert e["used"] == ["labels", "v1"] and g["method"] == "weight"
    assert e["theta"] == pytest.approx(g["est"]["theta"]) and e["se"] == pytest.approx(g["est"]["se"])
    assert e["certain"] == g["p_cn"] and e["exact_share"] == pytest.approx(g["exact_share"])


def test_rows_certain_to_be_labelled_count_exactly():
    w = np.r_[1000.0, 500.0, np.ones(400)]
    y = np.r_[1.0, 0.0, (np.arange(400) % 4 == 0).astype(float)]
    a = np.r_[0.0, 1.0, (np.arange(400) % 3 == 0).astype(float)]
    u = np.r_[0.9, 0.9, np.linspace(0.001, 0.999, 400)]
    lab = u / w < 0.2                                     # the run up to ξ0 = 0.2: both heavy rows are certain
    s = multiweight.sums("all", w, a, np.full(len(w), NAN), np.where(lab, y, NAN), u)
    assert s["c_n"] == 2 and s["c_wy"] == 1000.0 and s["wr"] == 400.0
    every = multiweight.estimate(multiweight.sums("all", w, a, a, y, u), Z)
    assert every["exact"] and every["theta"] == pytest.approx((w * y).sum() / w.sum())


def test_the_plan_is_multifidelity_monte_carlo_when_no_row_is_certain():
    """Equal weights and small samples: no row is certain, and the cheapest run of V is double sampling's (#27):
    ξ1 / ξ0 = √(c0 σ²1 / (c1 σ²2)), at the target."""
    N = 10 ** 7
    s = {"weight": float(N), "rows": N, "w2": float(N), "xi0": 30 / N, "xi1": 30 / N}
    est = {"exact": False, "reliable": True, "se": 0.05, "drawn": 30, "drawn_with": 9, "share_R": 0.3,
           "s1": 0.04, "s2": 0.06, "s2a": 0.10}
    plans = multiweight.plan(s, est, {"thr": 2.0, "rows": []}, 0.02, Z, 1.0, 1e-4)
    best = plans[0]
    assert best["kind"] == "mix"
    assert best["xi1"] / best["xi0"] == pytest.approx(math.sqrt(1.0 * 0.04 / (1e-4 * 0.06)), rel=0.15)
    V = (0.02 / Z) ** 2
    var = 0.04 * (1 / (best["xi1"] * N)) + 0.06 * (1 / (best["xi0"] * N))   # G(ξ) / W² ≈ 1 / (ξ N) here
    assert var == pytest.approx(V, rel=0.02)
    active = next(p for p in plans if p["kind"] == "active")
    assert active["labels"] == pytest.approx(0.10 / V, rel=0.02) and best["cost"] < active["cost"]
    free = multiweight.plan(s, est, {"thr": 2.0, "rows": []}, 0.02, Z, 1.0, 0.0)[0]
    assert free["kind"] == "every row" and math.isinf(free["xi1"])       # a free V judges every row


def test_a_pilot_first_then_v_on_the_labels_then_nothing_more():
    rng = np.random.default_rng(31)
    w, y, good, cheap = _population(rng, 20000)
    heavy = {"thr": 0.0, "rows": [(float(x), 0) for x in sorted(w, reverse=True)[:groups.HEAVY_CAP]]}
    u, lab, run = _draw(rng, w, 10, 10)
    plan = lambda lab, run, t: multiweight.plan(*(lambda s: (s, multiweight.estimate(s, Z)))(multiweight.sums(
        "all", w, cheap, np.where(run, good, NAN), np.where(lab, y, NAN), u)), heavy, t, Z, 1.0, 1e-4)
    first = plan(lab, run, 0.03)[0]
    assert first["kind"] == "pilot" and first["xi1"] >= first["xi0"] and first["more_labels"] > 0
    r = u / w
    pilot = r < first["xi0"]
    assert (~np.isnan(np.where(pilot, y, NAN))).sum() >= 30
    second = plan(pilot, np.zeros(len(w), bool), 0.03)[0]
    assert second["kind"] == "pilot" and second["more_labels"] == 0 and second["more_rows"] > 0
    done = plan(r < 1e9, r < 1e9, 0.03)[0]                # every row labelled
    assert done["kind"] == "met" and done["cost"] == 0


def test_below_the_floor_the_labels_and_the_active_version_get_a_betting_interval():
    """A rare answer, 60 labels drawn by weight with fewer than 5 rows with it (#31): `bet` gives the labels and the
    active version a betting interval, which holds, and the plan for them alone takes no more labels than the floor's;
    its inflation keeps to the usual interval's spread, not betting's."""
    rng = np.random.default_rng(314)
    w = np.floor(rng.pareto(1.2, 6000)) + 1
    y = (rng.random(6000) < 0.015).astype(float)
    good = np.where(rng.random(6000) < 0.95, y, 1 - y)
    cheap = np.where(rng.random(6000) < 0.9, y, 1 - y)
    heavy = {"thr": 0.0, "rows": [(float(x), 0) for x in sorted(w, reverse=True)[:groups.HEAVY_CAP]]}
    truth, hits, fewer = (w * y).sum() / w.sum(), [], []
    for _ in range(30):
        u, lab, run = _draw(rng, w, 60, 200)
        s = multiweight.sums("all", w, cheap, np.where(run, good, NAN), np.where(lab, y, NAN), u)
        e = multiweight.estimate(s, Z)
        if e["reliable"] or e["exact"]:
            continue
        before = multiweight.plan(s, multiweight.estimate(s, Z), heavy, 0.02, Z, 1.0, 1e-5, bet=False)
        multiweight.bet({"s": s, "est": e}, groups.run_rows(w, cheap, y, u, s["xi0"]), Z)
        assert e["interval"] == "betting" and e["reliable"] and e["ci"][0] <= e["theta"] <= e["ci"][1]
        hits.append(e["ci"][0] <= truth <= e["ci"][1])
        after = multiweight.plan(s, e, heavy, 0.02, Z, 1.0, 1e-5)
        pick = lambda ps: next(p for p in ps if p["kind"] in ("active", "met", "pilot", "learn"))
        fewer.append(pick(after)["labels"] <= pick(before)["labels"])
    assert len(hits) >= 15 and np.mean(hits) >= 0.95 and all(fewer)
