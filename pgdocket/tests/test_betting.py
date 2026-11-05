import math

import numpy as np

from pgdocket import betting, groups
from pgdocket.estimate import norm_ppf, ppi_share

Z = norm_ppf(0.975)


def _population(rng, N, share, agree):
    y = (rng.random(N) < share).astype(float)
    return y, np.where(rng.random(N) < agree, y, 1 - y)


def test_a_rare_answer_is_covered_at_any_number_of_labels():
    """2 % of 1,000 rows, 60 labels: the normal interval holds in well under 95 % of draws (it needs 10 labelled rows
    with the answer, about 500 labels here), the betting interval in 95 % or more."""
    rng = np.random.default_rng(30)
    y, f = _population(rng, 1000, 0.02, 0.95)
    truth = y.mean()
    bet, normal = [], []
    for _ in range(300):
        idx = rng.permutation(1000)[:60]
        lo, hi = betting.interval(y[idx], f[idx], f.sum(), 1000)
        bet.append(lo <= truth <= hi)
        lab = np.zeros(1000, bool)
        lab[idx] = True
        r = ppi_share(f[~lab], f[lab], y[lab])
        normal.append(r["estimate"] is not None and r["ci_low"] <= truth <= r["ci_high"])
    assert np.mean(bet) >= 0.95 and np.mean(normal) < 0.85


def test_a_common_answer_is_covered_but_wider_than_the_usual_interval_which_it_leaves_alone():
    rng = np.random.default_rng(31)
    y, f = _population(rng, 2000, 0.3, 0.9)
    truth, hits, ratio = y.mean(), [], []
    for _ in range(150):
        idx = rng.permutation(2000)[:400]
        lo, hi = betting.interval(y[idx], f[idx], f.sum(), 2000)
        hits.append(lo <= truth <= hi)
        lab = np.zeros(2000, bool)
        lab[idx] = True
        r = ppi_share(f[~lab], f[lab], y[lab])
        ratio.append((hi - lo) / (r["ci_high"] - r["ci_low"]))
    assert np.mean(hits) >= 0.95 and 1.2 < np.mean(ratio) < 2.0          # hence the normal interval above the floor


def test_every_row_labelled_is_exact_and_the_interval_closes_in_as_labels_run_out():
    y = np.r_[np.ones(5), np.zeros(45)]
    f = y.copy()
    assert betting.interval(y, f, f.sum(), 50) == (0.1, 0.1)
    rng = np.random.default_rng(32)
    order = rng.permutation(50)
    widths = [np.subtract(*betting.interval(y[order[:n]], f[order[:n]], f.sum(), 50)[::-1]) for n in (10, 30, 49)]
    assert widths[0] > widths[1] > widths[2] and widths[2] <= 0.02 + 1e-12     # one row left: one share in two


def test_labels_for_a_rare_answer_are_far_below_the_floor():
    rows, share = 5000, 0.01
    n = betting.labels_for(0.02, rows, rows * share, share, share)            # a model no better than chance
    assert n < 0.4 * math.ceil(10 / share) and n == betting.labels_for(0.02, rows, rows * share, share, share)
    y, f = betting.typical(n, rows, rows * share, share, share)
    lo, hi = betting.interval(y, f, rows * share, rows)
    assert (hi - lo) / 2 <= 0.02 and len(y) == n
    assert betting.labels_for(0.001, 300, 3, 0.01, 0.01) == 300                # no number of labels short of all


def _group(rng, name, N, share, agree, n):
    y, f = _population(rng, N, share, agree)
    lab = np.zeros(N, bool)
    lab[rng.permutation(N)[:n]] = True
    return groups.sums(name, np.ones(N), f, y, lab, lab), y, f, lab


def test_a_rare_answers_group_plans_on_betting_and_a_common_ones_on_the_usual_interval():
    rng = np.random.default_rng(33)
    rare, *_ = _group(rng, "rare", 4000, 0.01, 0.97, 200)
    common, *_ = _group(rng, "common", 4000, 0.3, 0.9, 200)
    gs = groups.summarise([rare, common], Z)
    p = {x["group"]: x for x in groups.plan(gs, 0.02, Z)["groups"]}
    r = next(g for g in gs if g["g"] == "rare")
    floor = math.ceil(10 / max(r["est"]["theta"] if r["est"] else 0, 1 / 202))
    assert p["rare"]["need"] < 0.5 * floor                                   # betting gets there sooner
    c = next(g for g in gs if g["g"] == "common")
    width = max(math.ceil(Z * Z * c["est"]["res_var"] / 0.02 ** 2), math.ceil(200 * (Z * c["est"]["se"] / 0.02) ** 2), 220)
    assert p["common"]["need"] == max(200, width)                            # the usual interval, as before


def test_a_group_below_the_floor_gets_a_betting_interval_that_holds():
    rng = np.random.default_rng(34)
    hits = []
    for _ in range(100):
        s, y, f, lab = _group(rng, "g", 3000, 0.02, 0.95, 150)
        g = groups.summarise([s], Z)[0]
        assert not g["reliable"]
        order = np.flatnonzero(lab)
        rng.shuffle(order)
        groups.bet(g, y[order], f[order], Z)
        assert g["reliable"] and g["interval"] == "betting" and g["ci"][0] <= g["est"]["theta"] <= g["ci"][1]
        hits.append(g["ci"][0] <= y.mean() <= g["ci"][1])
    assert np.mean(hits) >= 0.95


def test_labels_that_hold_every_row_with_the_answer_keep_the_truth():
    """Once the labels hold every row with the answer, none is left: the true share k / N must stay possible, though
    N · (k / N) − k is not exactly 0 in floating point (it rejected the truth, and once every share, before #30
    shipped)."""
    rng = np.random.default_rng(35)
    for N, k in ((984, 31), (336, 51), (420, 27), (2000, 1001)):         # N · (k / N) < k in floating point
        y = np.zeros(N)
        y[rng.choice(N, k, replace=False)] = 1
        f = y.copy()
        order = np.r_[np.flatnonzero(y), rng.permutation(np.flatnonzero(y == 0))][:N - 3]   # all but three noes
        rng.shuffle(order)
        ci = betting.interval(y[order], f[order], f.sum(), N)
        assert ci is not None and ci[0] <= k / N <= ci[1]


# ---------------------------------------------------------------- weighted shares (#31)
def _weighted(rng, N, share, agree, tail=1.2):
    """Heavy-tailed weights, as a join's fan-out gives them."""
    y, f = _population(rng, N, share, agree)
    return np.floor(rng.pareto(tail, N) + 1), y, f


def _ranked(w, y, f, u, n):
    """The first n rows of the ranking by u / w, as `groups.drawn_rows` gives them, and ξ."""
    xi = np.sort(u / w)[n]
    return groups.run_rows(w, f, y, u, xi), xi


def test_the_next_rows_hazard_is_the_sum_over_every_row_not_yet_drawn():
    rng = np.random.default_rng(310)
    w, y, f = _weighted(rng, 2000, 0.03, 0.9)
    u = rng.random(2000)
    r, xi = _ranked(w, y, f, u, 150)
    t = r["u"] / r["w"]
    order = np.argsort(u / w)
    exact = [(w[order[k:]] / (1 - t[k] * w[order[k:]])).sum() for k in range(150)]
    assert np.allclose(betting.hazard(r["w"], t, r["heavy"], r["powers"], xi), exact, rtol=1e-10)
    assert len(r["heavy"]) and len(r["heavy"]) < 2000 - 150                  # some rows one by one, most by the series


def test_labels_drawn_by_weight_bet_fairly_and_hold_a_rare_weighted_share():
    """Rows drawn in order of u / w: given the rows before, each score's mean is what the true share implies (and not
    what a share 5 points off implies), and the interval holds a 3 % weighted share at 60 labels, below the floor of 5
    rows drawn with the answer, in 95 % of draws or more."""
    rng = np.random.default_rng(311)
    w, y, f = _weighted(rng, 1500, 0.03, 0.9)
    W, M, truth = w.sum(), (w * f).sum(), (w * y).sum() / w.sum()
    fair, off, hits = [], [], []
    for _ in range(400):
        u = rng.random(len(w))
        r, xi = _ranked(w, y, f, u, 60)
        ci = betting.sps(r["y"], r["f"], r["w"], r["u"], W, M, r["heavy"], r["powers"], xi)
        hits.append(ci is not None and ci[0] <= truth <= ci[1])
        R = W - betting._before(r["w"])
        rho = R / betting.hazard(r["w"], r["u"] / r["w"], r["heavy"], r["powers"], xi)
        mf = (M - betting._before(r["w"] * r["f"])) / R
        z = (1 - r["u"]) * (r["y"] - 0.5 * r["f"] + 0.5) / 1.5                 # λ = ½
        for theta, out in ((truth, fair), (truth + 0.05, off)):
            my = (theta * W - betting._before(r["w"] * r["y"])) / R
            out.append(z - rho * (my - 0.5 * mf + 0.5) / 1.5)
    assert abs(np.mean(fair)) < 0.005 and np.mean(off) < -0.02
    assert np.mean(hits) >= 0.95


def test_rows_drawn_by_weight_that_hold_all_the_weight_give_the_share_exactly():
    w, y = np.array([5.0, 1, 1, 2]), np.array([1.0, 0, 1, 0])
    u = np.array([0.1, 0.2, 0.3, 0.4])
    assert betting.sps(y, y, w, u, w.sum(), w @ y, [], [0.0] * betting.POWERS, math.inf) == (6 / 9, 6 / 9)
    assert betting.weighted(y, y, w, w.sum(), w @ y, 4, 0.0) == (6 / 9, 6 / 9)


def test_labels_drawn_at_random_with_weights_hold_and_allow_for_a_heavy_row_not_yet_drawn():
    rng = np.random.default_rng(312)
    N = 1000
    w = rng.integers(1, 6, N).astype(float)                              # amounts of 1 to 5: betting can narrow
    y, f = _population(rng, N, 0.05, 0.9)
    truth, hits, widths = (w * y).sum() / w.sum(), [], []
    for _ in range(200):
        idx = rng.permutation(N)
        s, rest = idx[:150], idx[150:]
        lo, hi = betting.weighted(y[s], f[s], w[s], w.sum(), (w * f).sum(), N, w[rest].max())
        hits.append(lo <= truth <= hi)
        widths.append((hi - lo) / 2)
    assert np.mean(hits) >= 0.95 and np.mean(widths) < 0.08
    w[0] = w[1:].sum()                                                    # one row as heavy as all the others, not drawn
    s = 1 + rng.permutation(N - 1)[:150]
    lo, hi = betting.weighted(y[s], f[s], w[s], w.sum(), (w * f).sum(), N, w[0])
    assert lo < 0.05 and hi > 0.5                                         # it may or may not have the answer


def test_a_weighted_group_below_its_floor_gets_a_betting_interval_and_plans_on_it():
    """Labels drawn by weight with fewer than 5 rows with the answer (#26's floor): the group gets betting's interval,
    which holds, and the plan for ± 2 points draws no more rows than the floor alone would have it draw."""
    rng = np.random.default_rng(313)
    w, y, f = _weighted(rng, 3000, 0.015, 0.95)
    truth, hits, fewer = (w * y).sum() / w.sum(), [], []
    top = {groups.ALL: {"thr": 0.0, "rows": sorted(((float(a), int(b)) for a, b in zip(w, f)), reverse=True)}}
    for _ in range(40):
        u = rng.random(3000)
        lab = np.zeros(3000, bool)
        lab[np.argsort(u / w)[:80]] = True
        g = groups.summarise([groups.sums(groups.ALL, w, f, y, lab, np.zeros(3000, bool), u)], Z)[0]
        if g["method"] != "weight" or g["reliable"]:
            continue
        floor = groups.plan([g], 0.02, Z, top, bet=False)["groups"][0]
        groups.bet_drawn(g, groups.run_rows(w, f, y, u, g["p_xi"]), Z)
        assert g["interval"] == "betting" and g["reliable"] and g["ci"][0] <= g["est"]["theta"] <= g["ci"][1]
        hits.append(g["ci"][0] <= truth <= g["ci"][1])
        fewer.append(groups.plan([g], 0.02, Z, top)["groups"][0]["draw"] <= floor["draw"])
    assert len(hits) >= 20 and np.mean(hits) >= 0.95 and all(fewer)
