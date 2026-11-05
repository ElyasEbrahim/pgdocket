"""Several models for a weighted share (`pgdocket estimate --with V --weight EXPR`, #29).

A weighted share Σ w·y / Σ w is best labelled by weight (#26): rows are ranked by r = u / w, u a permanent random
number from the key (`groups.prn`), and the first rows are labelled, the heaviest certain to be. A second version V,
better than the active one or cheaper than people, can judge a longer run of the same ranking, so that its rows hold
the labelled ones, while the active version judges every row. That is two-phase sampling with probability in proportion
to weight (Särndal, Swensson & Wretman, 1992, ch. 9): V's run is the first phase, the labels inside it the second, and
the active version a census.

At ξ1 ≥ ξ0, the r of the first row V hasn't judged and of the first row without a label, row i is in V's run with
probability π1 = min(1, ξ1·w) and labelled with π0 = min(1, ξ0·w). Rows with ξ0·w ≥ 1 were certain to be labelled and
count exactly. The rest, R, is estimated by the two-phase regression estimator
    θ̂_R = ȳ0 − b·(x̄0 − x̃),   x = (v, a), V's and the active version's 0/1 answers:
- ȳ0, x̄0: plain means over R's labelled rows, which, drawn in proportion to weight, estimate R's weighted means
  (Hájek estimators);
- b: the regression of y on x there, with half a disagreement added each way, as in `multi.covariance`;
- x̃: R's weighted mean of a, known from every row, and of v, from v's regression on a over V's run (weights w / π1)
  at a's mean.
Its variance, linearised, weights each phase's residuals by 1 − π:
    Var θ̂_R ≈ Σ_run (1 − π1)(w/π1)² e1² / Ŵ1² + Σ_labelled (1 − π0) e2² / n0²,
e1 = b_v·(v − E[v | a]) on V's run and e2 = y − b0 − b·x on the labelled rows. Until the labelled rows hold MIN_EACH_SIDE
with and without the answer, all inside V's run, V is left out and the estimate is #26's from the same labels: in the
CFPB join's draws with fewer, #26's held more often than this one without V (88–98 % against 81–94 %).

**The plan.** With σ²1 and σ²2 the variances of e1 and e2, thresholds (ξ0, ξ1) give
    W² · Var θ̂ ≈ σ²1·G(ξ1) + σ²2·G(ξ0),   G(ξ) = Σ_{ξ·w < 1} w·(1/ξ − w),
for Σ min(1, ξ0·w) labels and Σ min(1, ξ1·w) rows judged by V. The cheapest pair for ± target is found by search.
With no certain rows and small samples it is multifidelity Monte Carlo's nested plan (#27), in the population where a
row appears as often as its weight.

**Below the floor (#31).** A group whose labelled rows drawn hold fewer than DRAWN_EACH_SIDE with or without the answer
gets a betting interval from its labels and the active version (`betting.sps`, as `estimate --weight` gives it), and the
plan for the labels and the active version alone takes whichever reaches the target first, the floor or betting. V
counts only past MIN_EACH_SIDE each way, so it plays no part there.
"""
import bisect
import math

import numpy as np
from psycopg import sql

from . import betting
from .estimate import MIN_EACH_SIDE
from .groups import DRAWN_EACH_SIDE, PILOT, _alpha, sps_estimate

INF = math.inf
_L0 = ("n", "y", "a", "ya")                         # sums over R's labelled rows (y, a: 0/1)
_L01 = ("n", "y", "v", "a", "yv", "ya", "av")       # the same where V judged them too
_RUN = ("m", "H", "Hv", "Ha", "Hva", "D", "Dv", "Da", "Dva")


def _pair(s, x1, x2):
    return s[x1] if x1 == x2 else s["".join(sorted(x1 + x2))]


def _fit(m, q, xs):
    """Least squares of y on 1 and the 0/1 answers `xs`, from sums over R's labelled rows: `m` plain, `q` weighted by
    1 − π0. Two pseudo-rows of weight ½, (y 1, every x 0) and (y 0, every x 1), add half a disagreement each way: a
    model that agreed with every labelled row would otherwise claim no error. Returns (b0, b, the residual sum of
    squares weighted by 1 − π0, the residual variance), both with the pseudo-rows."""
    k = len(xs)
    XtX, Xty = np.zeros((k + 1, k + 1)), np.zeros(k + 1)
    XtX[0, 0], Xty[0] = m["n"] + 1, m["y"] + 0.5
    for i, x1 in enumerate(xs, 1):
        XtX[0, i] = XtX[i, 0] = m[x1] + 0.5
        Xty[i] = m["y" + x1]
        for j, x2 in enumerate(xs, 1):
            XtX[i, j] = _pair(m, x1, x2) + 0.5
    beta = np.linalg.lstsq(XtX, Xty, rcond=None)[0]       # the two versions may agree on every row
    b0, b = float(beta[0]), [float(x) for x in beta[1:]]

    def rss(s):                                         # Σ (y − b0 − b·x)² from 0/1 sums (y² = y, x² = x)
        t = s["y"] * (1 - 2 * b0) + b0 * b0 * s["n"]
        for bi, x1 in zip(b, xs):
            t += 2 * bi * (b0 * s[x1] - s["y" + x1])
            t += sum(bi * bj * _pair(s, x1, x2) for bj, x2 in zip(b, xs))
        return t

    pseudo = 0.5 * ((1 - b0) ** 2 + (b0 + sum(b)) ** 2)
    n = m["n"] + 1
    weighted = rss(q) + pseudo * (q["n"] / m["n"] if m["n"] else 1.0)
    return b0, b, max(0.0, weighted), max(0.0, rss(m) + pseudo) / max(1.0, n - k - 1)


def estimate(s, z, names=("v1", "v2")):
    """The weighted share from labels drawn by weight, the active version on every row and V on its run, as above.
    Returns theta, se, ci, exact, reliable (at least DRAWN_EACH_SIDE labelled rows drawn with and without the answer),
    used (the sources the regression kept: V needs MIN_EACH_SIDE each way among the labelled rows, all inside its run,
    and to differ from the active version there), V's estimated share, and for the plan the residual variances: s2a of
    y given the active version, s2 given both, s1 of e1. None for a group with no weight."""
    W, N, L0, Q0 = s["weight"], s["rows"], s["L0"], s["Q0"]
    if not N or W <= 0:
        return None
    out = {"labels": int(round(s["c_n"] + L0["n"])), "drawn": int(round(L0["n"])), "drawn_with": int(round(L0["y"])),
           "certain": int(s["c_n"]), "exact_share": s["c_w"] / W,
           "run": s["run_all"], "used": ["labels"], "v_share": None, "s1": None, "s2": None, "s2a": None,
           "share_R": None, "exact": False, "reliable": False, "theta": None, "se": None, "ci": None,
           "v_reason": None}
    if s["wr"] <= 0:                                    # every row certain to be labelled
        t = s["c_wy"] / W
        return {**out, "theta": t, "se": 0.0, "ci": (t, t), "exact": True, "reliable": True}
    n0, k = L0["n"], L0["y"]
    if n0 < 2:
        return out
    *_, s2a = _fit(L0, Q0, ["a"])
    out.update(s2a=s2a, share_R=(k + 0.5) / (n0 + 1), used=["labels", names[0]])
    if k in (0, n0):                                     # every label the same so far: the plan still needs s2a
        return out
    L1, Q1, run = s["L01"], s["Q01"], s["run"]
    differ = run["Hv"] + run["Ha"] - 2 * run["Hva"] > 1e-12 * max(1.0, run["H"])
    use_v = s["xi1"] >= s["xi0"] and L1["n"] == n0 and min(L1["y"], n0 - L1["y"]) >= MIN_EACH_SIDE and differ
    out["v_reason"] = (None if use_v else "run" if s["xi1"] < s["xi0"] else "same" if not differ else "few")
    a = s["wr"] / W
    if use_v:
        A = s["wra"] / s["wr"]                           # the active version's weighted share of R, from every row
        _, (bv, bva), rss, s2 = _fit(L1, Q1, ["v", "a"])
        H, Ha = run["H"], run["Ha"]
        mean = run["Hv"] / H
        p1 = run["Hva"] / Ha if Ha > 0 else mean          # V's share among R's rows the active version says yes to
        p0 = (run["Hv"] - run["Hva"]) / (H - Ha) if H - Ha > 0 else mean
        vt = p0 + (p1 - p0) * A                            # V's weighted share of R, from its run and a's census
        theta_R = (L1["y"] - bv * L1["v"] - bva * L1["a"]) / n0 + bv * vt + bva * A
        e1 = bv * bv * (run["Dva"] * (1 - 2 * p1) + p1 * p1 * run["Da"]
                        + (run["Dv"] - run["Dva"]) * (1 - 2 * p0) + p0 * p0 * (run["D"] - run["Da"])) / (H * H)
        var_R = max(0.0, e1) + rss / ((n0 + 1) * max(1.0, n0 - 2))
        s1 = bv * bv * (Ha * p1 * (1 - p1) + (H - Ha) * p0 * (1 - p0)) / H
        out.update(s1=s1, s2=s2, used=["labels", names[0], names[1]], v_share=(s["c_wv"] + s["wr"] * vt) / W)
        theta, se = s["c_wy"] / W + a * theta_R, a * math.sqrt(var_R)
    else:                                                # without V: #26's estimate from the same labels
        g = sps_estimate({"judged": N, "weight": W, "p_wr": s["wr"], "p_n": n0, "p_k": k, "p_f": L0["a"],
                          "p_yf": L0["ya"], "p_cwy": s["c_wy"], "p_wrf": s["wra"]})
        theta, se = g["theta"], g["se"]
        theta_R = (theta - s["c_wy"] / W) / a
    return {**out, "theta": theta, "se": se, "ci": (max(0.0, theta - z * se), min(1.0, theta + z * se)),
            "reliable": min(k, n0 - k) >= DRAWN_EACH_SIDE, "share_R": theta_R}


def sums(g, w, a, v, y, u):
    """The statistics `ledger_sums` returns for one group, from arrays over its rows in scope: weight, the active
    version's 0/1 answer, V's (NaN where it hasn't judged), the 0/1 label (NaN where there is none), the permanent random
    number. For the harness and tests."""
    w, a, v, y, u = (np.asarray(x, float) for x in (w, a, v, y, u))
    keep = w > 0
    w, a, v, y, u = w[keep], a[keep], v[keep], y[keep], u[keep]
    r = u / w
    has_y, has_v = ~np.isnan(y), ~np.isnan(v)
    xi0 = float(r[~has_y].min()) if (~has_y).any() else INF
    xi1 = float(r[~has_v].min()) if (~has_v).any() else INF
    y, v = np.nan_to_num(y), np.nan_to_num(v)
    c0, c1 = xi0 * w >= 1, xi1 * w >= 1
    lab, run = (r < xi0) & ~c0, (r < xi1) & ~c0
    q = np.where(c0, 0.0, 1 - np.where(c0, 0.0, xi0 * w))
    h = w / np.minimum(1.0, xi1 * w)
    dd = np.where(c1, 0.0, (1 - np.where(c1, 0.0, xi1 * w)) / (1.0 if math.isinf(xi1) else xi1 * xi1))
    S = lambda m, x: float(x[m].sum())
    one = np.ones(len(w))
    cols = {"y": y, "v": v, "a": a, "ya": y * a, "yv": y * v, "av": a * v}
    both = lab & run
    return {"g": g, "negative": 0, "xi0": xi0, "xi1": xi1, "rows": int(len(w)), "weight": float(w.sum()),
            "w2": float((w * w).sum()), "wa": float((w * a).sum()), "vjudged": int(has_v.sum()),
            "run_all": int((r < xi1).sum()), "c_n": int(c0.sum()), "c_w": S(c0, w), "c_wy": S(c0, w * y),
            "c_wv": S(c0, w * v), "wr": S(~c0, w), "wra": S(~c0, w * a),
            "L0": {key: S(lab, one if key == "n" else cols[key]) for key in _L0},
            "Q0": {key: S(lab, q * (one if key == "n" else cols[key])) for key in _L0},
            "L01": {key: S(both, one if key == "n" else cols[key]) for key in _L01},
            "Q01": {key: S(both, q * (one if key == "n" else cols[key])) for key in _L01},
            "run": {"m": int(run.sum()), "H": S(run, h), "Hv": S(run, h * v), "Ha": S(run, h * a),
                    "Hva": S(run, h * v * a), "D": S(run, dd), "Dv": S(run, dd * v), "Da": S(run, dd * a),
                    "Dva": S(run, dd * v * a)}}


# ---------------------------------------------------------------- the cheapest thresholds for a target
def plan(s, est, heavy, target, z, label_cost, cost_v, grid=72, with_v=True, bet=True):
    """The cheapest (ξ0, ξ1) for ± `target`, among: labels and the active version alone; V on every row; and V on a
    run of the ranking (ratios ξ1 / ξ0 on a grid), each with the smallest ξ0 that reaches the target and expects
    DRAWN_EACH_SIDE labelled rows drawn with and without the answer. `heavy` is the group's heaviest rows as
    `groups.heaviest_rows` gives them: every row lighter counts as drawn, not certain. Work done counts as paid for.
    A group with fewer than PILOT labelled rows drawn gets a pilot first, V judging those rows; one whose V hasn't
    judged its labelled run has V judge it first; one already within the target needs nothing more. Returns the
    plans, cheapest first, each with its thresholds, labels and V's rows expected, what is still needed and its cost.
    `with_v` False plans the labels for the active version alone (#26's design), for comparison. Below
    DRAWN_EACH_SIDE each way, the labels with the active version alone may reach the target sooner by betting (`bet`):
    how many rows drawn that takes is judged on a typical draw from the rows not certain (`betting.labels_for`)."""
    W, N, W2 = s["weight"], s["rows"], s["w2"]
    xi0, xi1 = s["xi0"], s["xi1"]
    hw = sorted((w for w, _ in heavy["rows"]), reverse=True)
    neg, cw, cw2 = [-w for w in hw], [0.0], [0.0]
    for w in hw:
        cw.append(cw[-1] + w)
        cw2.append(cw2[-1] + w * w)

    def at(xi):                                         # certain rows at ξ, and the weight and Σ w² of the rest
        j = bisect.bisect_right(neg, -1 / xi)
        return j, max(0.0, W - cw[j]), max(0.0, W2 - cw2[j])

    def G(xi):
        return 0.0 if math.isinf(xi) else max(0.0, at(xi)[1] / xi - at(xi)[2])

    def count(xi):
        if math.isinf(xi):
            return N
        j, sw, _ = at(xi)
        return min(N, j + xi * sw)

    def drawn(xi):                                      # expected rows drawn, not certain
        return 0.0 if math.isinf(xi) else xi * at(xi)[1]

    now0, now1 = count(xi0), count(xi1)

    def make(kind, x0, x1):
        if count(x0) >= N and kind != "met":
            kind, x0 = "exact", INF
        x1 = max(x1, xi1)
        more0, more1 = max(0.0, count(x0) - now0), max(0.0, count(x1) - now1)
        return {"kind": kind, "xi0": x0, "xi1": x1, "labels": math.ceil(count(x0) - 1e-6),
                "rows": math.ceil(count(x1) - 1e-6),
                "more_labels": math.ceil(more0 - 1e-9), "more_rows": math.ceil(more1 - 1e-9),
                "cost": label_cost * more0 + cost_v * more1}

    def smallest(ok):                                   # the smallest ξ0 ≥ the current one with ok(ξ0), else ∞
        lo = xi0
        if ok(lo):
            return lo
        hi = lo
        for _ in range(400):
            hi *= 1.5
            if ok(hi) or count(hi) >= N:
                break
        if not ok(hi):
            return INF
        for _ in range(60):
            mid = math.sqrt(lo * hi)
            lo, hi = (lo, mid) if ok(mid) else (mid, hi)
        return hi

    if est and (est["exact"] or (est["reliable"] and z * est["se"] <= target)):
        return [{**make("met", xi0, xi1), "more_labels": 0, "more_rows": 0, "cost": 0.0}]
    n0 = est["drawn"] if est else 0
    if n0 < PILOT:                                      # a pilot, or the rest of one that fell short
        need = drawn(xi0) + PILOT - n0
        x0 = smallest(lambda x: drawn(x) >= need)
        return [make("pilot", x0, x0 if with_v else xi1)]
    if xi1 < xi0 and with_v:                            # V must judge the labelled rows before it can be weighed
        return [make("pilot", xi0, xi0)]
    V = (target / z) ** 2 * W * W
    k = est["drawn_with"]
    p = min(max(est["share_R"], 1 / (n0 + 2)), 1 - 1 / (n0 + 2))

    def floor(each):                                    # rows drawn with and without the answer: `each` expected,
        short = each - min(k, n0 - k)                   # and any still missing on top of the rows drawn so far
        need = drawn(xi0) + short / min(p, 1 - p) if short > 0 else each / min(p, 1 - p)
        return lambda x: drawn(x) >= need

    s1, s2, s2a = est["s1"], est["s2"], est["s2a"]
    # the sample may show more variance than the formula implies at the current thresholds: plan for that much more
    now = (s1 * G(xi1) + s2 * G(xi0)) if s2 is not None else s2a * G(xi0)
    se = est.get("normal_se") if est.get("interval") else est["se"]     # a betting interval is wider by design
    if se and now > 0:
        f = max(1.0, (se * W) ** 2 / now)
        s2a, s1, s2 = s2a * f, s1 and s1 * f, s2 and s2 * f
    at5 = floor(DRAWN_EACH_SIDE)
    x_active = smallest(lambda x: s2a * G(x) <= V and at5(x))
    if bet and DRAWN_EACH_SIDE > min(k, n0 - k) and s["wr"] > 0:      # below the floor: would betting get there sooner?
        L0, rest = s["L0"], N - s["c_n"]
        q1, q0 = (L0["ya"] + 0.5) / (L0["a"] + 1), (L0["y"] - L0["ya"] + 0.5) / (L0["n"] - L0["a"] + 1)
        route = betting.labels_for(target * W / s["wr"], rest, s["wra"] / s["wr"] * rest, q1, q0, _alpha(z),
                                   start=n0 + 1)
        if est.get("interval"):                         # no fewer than its own betting interval so far implies
            route = max(route, math.ceil(n0 * (z * est["se"] / target) ** 2))
        more = drawn(xi0) + route - n0
        x_active = min(x_active, smallest(lambda x: drawn(x) >= more))
    plans = [make("active", x_active, xi1)]
    if not with_v:
        return _step(plans, n0, smallest, drawn, make)
    if s2 is None:
        if min(k, n0 - k) >= MIN_EACH_SIDE:             # V says what the active version says: it adds nothing
            return _step(plans, n0, smallest, drawn, make)
        # V can't be weighed until the labelled rows hold MIN_EACH_SIDE each way: labels until they do (or the active
        # version's target, if sooner: labelling more only to weigh V costs more than it saves on a rare answer),
        # V judging them too
        x0 = min(plans[0]["xi0"], smallest(floor(MIN_EACH_SIDE)))
        return _step([make("learn", x0, x0)] + plans, n0, smallest, drawn, make)
    plans.append(make("every row", smallest(lambda x: s2 * G(x) <= V and at5(x)), INF))
    for q in [1.0] + list(np.geomspace(1.2, 1e6, grid)):
        x0 = smallest(lambda x: s1 * G(max(xi1, q * x)) + s2 * G(x) <= V and at5(x))
        plans.append(make("mix", x0, q * x0 if not math.isinf(x0) else x0))
    return _step(sorted(plans, key=lambda p: (p["cost"], p["labels"])), n0, smallest, drawn, make)


def this_round(p):
    """What a plan asks for now: its first step, when it plans more than one round can take, else the whole of it."""
    return p.get("step") or p


def _step(plans, n0, smallest, drawn, make):
    """The first plan's step for this round: labels drawn by weight up to at most four times the rows drawn so far
    (and PILOT · 4 more), as #26's plan takes them, since rates measured on a small sample can ask for far too many;
    the next round plans again from what they show. V's run goes as planned, holding the step's labels."""
    best, most = plans[0], max(4 * n0, n0 + 4 * PILOT)
    if best["kind"] in ("met", "pilot") or drawn(best["xi0"]) <= most:
        return plans
    x0 = smallest(lambda x: drawn(x) >= most)
    if math.isinf(x0):
        return plans
    step = make(best["kind"], x0, max(best["xi1"], x0))
    plans[0] = {**best, "step": {"xi0": x0, "xi1": step["xi1"], "more_labels": step["more_labels"],
                                 "more_rows": step["more_rows"], "cost": step["cost"]}}
    return plans


# ---------------------------------------------------------------- database
def _aggs():
    """The SQL aggregates `ledger_sums` returns, as (name, expression over r, filter)."""
    cols = {"n": "1", "y": "r.y", "v": "r.v", "a": "r.a", "ya": "r.y * r.a", "yv": "r.y * r.v", "av": "r.a * r.v"}
    out = [("rows", "1", "true"), ("weight", "r.w", "true"), ("w2", "r.w * r.w", "true"), ("wa", "r.w * r.a", "true"),
           ("vjudged", "1", "r.has_v"), ("run_all", "1", "r.inrun"), ("c_n", "1", "r.c0"), ("c_w", "r.w", "r.c0"),
           ("c_wy", "r.w * r.y", "r.c0"), ("c_wv", "r.w * r.v", "r.c0"), ("wr", "r.w", "NOT r.c0"),
           ("wra", "r.w * r.a", "NOT r.c0")]
    out += [(f"L0_{k}", cols[k], "r.lab") for k in _L0] + [(f"Q0_{k}", f"r.q * {cols[k]}", "r.lab") for k in _L0]
    out += [(f"L01_{k}", cols[k], "r.lab AND r.run") for k in _L01]
    out += [(f"Q01_{k}", f"r.q * {cols[k]}", "r.lab AND r.run") for k in _L01]
    out += [("run_m", "1", "r.run"), ("run_H", "r.h", "r.run"), ("run_Hv", "r.h * r.v", "r.run"),
            ("run_Ha", "r.h * r.a", "r.run"), ("run_Hva", "r.h * r.v * r.a", "r.run"), ("run_D", "r.dd", "r.run"),
            ("run_Dv", "r.dd * r.v", "r.run"), ("run_Da", "r.dd * r.a", "r.run"),
            ("run_Dva", "r.dd * r.v * r.a", "r.run")]
    return out


def ledger_sums(conn, d, version, answer, where=None, weight=None, by=None, seed="1"):
    """One pass over the judged rows in scope (`groups._scope`: the active version's answer a, the weight, the label,
    the permanent random number), with V's current answer: per group, the sums `estimate` and `plan` take."""
    from .groups import _scope
    noul = d["spec"]["kind"] == "noul"
    fv = sql.SQL("(jv.score >= 0.5)::int" if noul else "(jv.value = %(ans)s)::int")
    aggs = sql.SQL(", ").join(sql.SQL("coalesce(sum({e}) FILTER (WHERE r.w IS NOT NULL AND {f}), 0) AS {n}").format(
        e=sql.SQL(e), f=sql.SQL(f), n=sql.Identifier(n)) for n, e, f in _aggs())
    q = sql.SQL("""
        WITH {scope},
        base AS (
            SELECT sc.g, sc.w, sc.u, sc.f AS a, {fv} AS v, sc.has_label, coalesce(sc.y, 0) AS y
            FROM scope sc
            LEFT JOIN docket.current cv ON cv.decision_id = %(d)s AND cv.version = %(vv)s AND cv.row_key = sc.k
            LEFT JOIN docket.judgment jv ON jv.id = cv.judgment_id
        ), cut AS (
            SELECT g, count(*) FILTER (WHERE w < 0) AS negative,
                   coalesce(min(u / w) FILTER (WHERE w > 0 AND NOT has_label), 'Infinity') AS xi0,
                   coalesce(min(u / w) FILTER (WHERE w > 0 AND v IS NULL), 'Infinity') AS xi1
            FROM base GROUP BY g
        ), r AS (
            SELECT b.g, b.w, b.a, coalesce(b.v, 0) AS v, b.v IS NOT NULL AS has_v, b.y,
                   c.xi0 * b.w >= 1 AS c0, b.u / b.w < c.xi1 AS inrun,
                   b.u / b.w < c.xi0 AND c.xi0 * b.w < 1 AS lab, b.u / b.w < c.xi1 AND c.xi0 * b.w < 1 AS run,
                   CASE WHEN c.xi0 * b.w < 1 THEN 1 - c.xi0 * b.w ELSE 0 END AS q,
                   b.w / least(1, c.xi1 * b.w) AS h,
                   CASE WHEN c.xi1 * b.w < 1 THEN (1 - c.xi1 * b.w) / (c.xi1 * c.xi1) ELSE 0 END AS dd
            FROM base b JOIN cut c ON c.g IS NOT DISTINCT FROM b.g WHERE b.w > 0
        )
        SELECT c.g, c.negative, c.xi0, c.xi1, {aggs}
        FROM cut c LEFT JOIN r ON r.g IS NOT DISTINCT FROM c.g
        GROUP BY c.g, c.negative, c.xi0, c.xi1 ORDER BY c.g NULLS LAST""").format(
        scope=_scope(d, by, where, weight), fv=fv, aggs=aggs)
    cur = conn.execute(q, {"ans": answer, "d": d["id"], "by": by, "seed": str(seed), "vv": int(version)})
    names = [c.name for c in cur.description]
    out = []
    for row in cur.fetchall():
        f = dict(zip(names, row))
        if f["negative"]:
            raise SystemExit(f"{d['name']}: --weight is negative on some rows of {f['g']}: weights must be ≥ 0")
        s = {k: (v if k == "g" else float(v)) for k, v in f.items() if "_" not in k or k in (
            "w2", "c_n", "c_w", "c_wy", "c_wv", "run_all")}
        for part, keys in (("L0", _L0), ("Q0", _L0), ("L01", _L01), ("Q01", _L01), ("run", _RUN)):
            s[part] = {k: float(f[f"{part}_{k}"]) for k in keys}
        s["rows"], s["c_n"], s["run_all"], s["vjudged"] = int(s["rows"]), int(s["c_n"]), int(s["run_all"]), int(s["vjudged"])
        out.append(s)
    return out


def _cuts(conn, d, cuts, by, where, weight, seed, answer, kind, version=None):
    """Rows of each group's ranking with r = u / w below its cut, `cuts` [(group, ξ)]: to queue for V ("queue") or
    to label ("draw"). The queue skips rows V has judged and the draw rows with a label."""
    from .groups import _scope
    cuts = [(g, x) for g, x in cuts if x > 0]
    if not cuts:
        return 0 if kind == "queue" else []
    head = sql.SQL("WITH want AS (SELECT * FROM unnest(%(g)s::text[], %(xi)s::float8[]) AS x(g, xi)), {scope}").format(
        scope=_scope(d, by, where, weight))
    rows = sql.SQL("FROM scope sc JOIN want x ON x.g IS NOT DISTINCT FROM sc.g WHERE sc.w > 0 AND sc.u / sc.w < x.xi")
    p = {"g": [g for g, _ in cuts], "xi": [float(x) for _, x in cuts], "ans": answer, "d": d["id"], "by": by,
         "seed": str(seed), "v": version}
    if kind == "queue":
        q = head + sql.SQL(" INSERT INTO docket.queue (decision_id, version, row_key) SELECT %(d)s, %(v)s, sc.k ") + rows \
            + sql.SQL(" AND NOT EXISTS (SELECT 1 FROM docket.current c WHERE c.decision_id = %(d)s AND c.version = %(v)s"
                      " AND c.row_key = sc.k) ON CONFLICT DO NOTHING")
        n = conn.execute(q, p).rowcount
        conn.execute("SELECT pg_notify('docket', %s)", (str(d["id"]),))
        return n
    q = head + sql.SQL(" SELECT sc.k, sc.g ") + rows + sql.SQL(" AND NOT sc.has_label ORDER BY sc.g NULLS LAST, sc.u / sc.w")
    return [(k, g, "by weight") for k, g in conn.execute(q, p).fetchall()]


def queue(conn, d, version, cuts, by=None, where=None, weight=None, seed="1", answer=None):
    """Queue V for each group's run of the ranking up to its ξ1, `cuts` [(group, ξ1)]. Returns rows queued."""
    return _cuts(conn, d, cuts, by, where, weight, seed, answer, "queue", version)


def draw(conn, d, cuts, by=None, where=None, weight=None, seed="1", answer=None):
    """The rows to label: each group's run of the ranking up to its ξ0 without a label, as [(key, group, 'by weight')]
    for `groups.export`; `label` imports them as drawn by weight."""
    return _cuts(conn, d, cuts, by, where, weight, seed, answer, "draw")


def estimate_with(conn, name, answer, wanted, weight, where=None, level=0.95, target=None, label_cost=None,
                  costs=None, by=None, seed="1"):
    """`pgdocket estimate NAME --answer A --with V --weight EXPR [--by COL]`: each group's weighted share from labels
    drawn by weight, the active version and V, and with --target and --label-cost each group's cheapest thresholds."""
    from .estimate import norm_ppf
    from .groups import decision, drawn_rows, heaviest_rows
    from .multi import version_info
    d = decision(conn, name)
    if d["spec"]["kind"] == "noul":
        answer = "yes"
    elif answer not in [o["name"] for o in d["spec"]["options"]]:
        raise SystemExit(f"estimate --with needs --answer, one of: {', '.join(o['name'] for o in d['spec']['options'])}")
    vs = version_info(conn, d, wanted, costs)
    if len(vs) != 2:
        raise SystemExit("with --weight, --with takes one version other than the active one: it judges a run of the "
                         "ranking by weight, the active version every row")
    z = norm_ppf(0.5 + level / 2)
    names = (f"v{vs[0]['version']}", f"v{vs[1]['version']}")
    stats = ledger_sums(conn, d, vs[1]["version"], answer, where, weight, by, seed)
    gs = [{"g": s["g"], "s": s, "est": estimate(s, z, names), "plan": None} for s in stats]
    unsure = {x["g"]: x["s"]["xi0"] for x in gs if x["est"] and not x["est"]["reliable"] and not x["est"]["exact"]
              and x["est"]["labels"] and math.isfinite(x["s"]["xi0"])}
    if unsure:                                          # below the floor: betting instead
        rows = drawn_rows(conn, d, by, answer, where, weight, unsure, seed)
        for x in gs:
            if x["g"] in unsure:
                bet(x, rows[x["g"]], z)
    totals = None
    if target and label_cost is not None:
        live = [x for x in gs if x["s"]["rows"]]
        # heavy rows down to a twentieth of what a label plan needs: V's runs are longer, and make more rows certain
        heavy = heaviest_rows(conn, d, by, answer, where, weight,
                              [{"g": x["g"], "weight": x["s"]["weight"], "judged": x["s"]["rows"]} for x in live],
                              target / 20, z, seed)
        for x in live:
            x["plan"] = plan(x["s"], x["est"], heavy.get(x["g"], {"thr": 0.0, "rows": []}), target, z, label_cost,
                             vs[1]["cost"])
        totals = {}
        for way in ("cheapest", "active", "every row"):
            chosen = [x["plan"][0] if way == "cheapest" else _pick(x["plan"], way) for x in live]
            if all(chosen):
                totals[way] = {k: sum(p[k] for p in chosen) for k in ("labels", "more_labels", "rows", "more_rows", "cost")}
    live = [x for x in gs if x["est"]]
    overall = None
    if len(live) > 1 and all(x["est"]["theta"] is not None for x in live):
        W = sum(x["s"]["weight"] for x in live)
        t = sum(x["s"]["weight"] / W * x["est"]["theta"] for x in live)
        se = math.sqrt(sum((x["s"]["weight"] / W * x["est"]["se"]) ** 2 for x in live))
        overall = {"theta": t, "se": se, "ci": (max(0.0, t - z * se), min(1.0, t + z * se)),
                   "reliable": all(x["est"]["reliable"] for x in live)}
    return {"name": name, "answer": answer, "level": level, "where": where, "by": by, "weight": weight, "seed": seed,
            "versions": vs, "groups": gs, "overall": overall, "target": target, "label_cost": label_cost,
            "totals": totals}


def bet(x, r, z):
    """Give a group below the floor a betting interval from its run of labels drawn by weight and the active version
    (`groups.drawn_rows`, `betting.sps`). The estimate stays the usual one, kept inside it (the run's mean where there is
    none); `se` becomes the half-width over z, for the plan and the all-groups line."""
    s, e = x["s"], x["est"]
    if not len(r["y"]):
        return
    ci = betting.sps(r["y"], r["f"], r["w"], r["u"], s["weight"], s["wa"], r["heavy"], r["powers"], s["xi0"], _alpha(z))
    if ci is None:
        return
    L0 = s["L0"]
    theta = e["theta"] if e["theta"] is not None else (
        (s["c_wy"] + s["wr"] * (L0["y"] / L0["n"] if L0["n"] else 0.0)) / s["weight"])
    e.update(theta=min(max(theta, ci[0]), ci[1]), ci=ci, normal_se=e["se"], se=(ci[1] - ci[0]) / (2 * z),
             reliable=True, interval="betting")


def _pick(plans, kind):
    """The plan of this kind, or the first if it is a pilot, a step or nothing more, which every way starts with."""
    if plans[0]["kind"] in ("met", "pilot", "learn", "exact"):
        return plans[0]
    return next((p for p in plans if p["kind"] == kind), None)


def render(r):
    """Plain-text report for the CLI."""
    pct = lambda x: "–" if x is None else f"{100 * x:.1f} %"
    ci = lambda e: "–" if not e or not e["ci"] else ("exact" if e["exact"] else f"{100 * e['ci'][0]:.1f} – {100 * e['ci'][1]:.1f}")
    lvl = f"{100 * r['level']:.0f} %"
    a, v = r["versions"]
    na, nv = f"v{a['version']}", f"v{v['version']}"
    name = lambda g: "(none)" if g is None else str(g)
    gs = [x for x in r["groups"] if x["est"]]
    L = [f"{r['name']}: share of the weight ({r['weight']}) whose true answer is {r['answer']!r}"
         + (f", by {r['by']}" if r["by"] else "") + f", from labels drawn by weight, {na} on every row and {nv} on a run "
         f"of the ranking ({lvl} intervals)" + (f", where {r['where']}" if r["where"] else ""),
         f"{'group':<18} {'rows':>9} {'labels':>7} {'exact':>6} {nv + ' rows':>9} {na + ' says':>8} {nv + ' says':>8} "
         f"{'estimate':>9} {lvl + ' interval':>16}"]
    for x in gs:
        e, s = x["est"], x["s"]
        L.append(f"{name(x['g']):<18.18} {s['rows']:>9,} {e['labels']:>7,} {100 * e['exact_share']:>4.0f} % "
                 f"{e['run']:>9,} {pct(s['wa'] / s['weight']):>8} {pct(e['v_share']):>8} {pct(e['theta']):>9} "
                 f"{ci(e):>16}" + ("" if e["reliable"] or e["theta"] is None else "  ⚠")
                 + ("  (betting)" if e.get("interval") else ""))
    o = r["overall"]
    if o:
        L.append(f"{'all groups':<18} {'':>9} {'':>7} {'':>6} {'':>9} {'':>8} {'':>8} {pct(o['theta']):>9} "
                 f"{ci({'exact': o['se'] == 0, 'ci': o['ci']}):>16}" + ("" if o["reliable"] else "  ⚠"))
    L.append(f"labels: the run at the top of the ranking by u / w (seed {r['seed']}); exact: the share of the weight in "
             f"rows certain to be labelled; {nv} rows: its run of the same ranking")
    why = {"run": "its run doesn't hold the labelled rows yet", "same": f"it says what {na} says on its run",
           "few": f"the labelled rows hold fewer than {MIN_EACH_SIDE} with and without the answer"}
    for reason, text in why.items():
        left = [name(x["g"]) for x in gs if x["est"]["theta"] is not None and x["est"]["v_reason"] == reason]
        if left:
            L.append(f"{nv} left out" + (f" in {', '.join(left)}" if r["by"] else "") + f": {text}")
    if any(x["est"].get("interval") for x in gs):
        L.append(f"(betting): fewer than {DRAWN_EACH_SIDE} rows drawn by weight with or without the answer, so the "
                 f"interval is by betting from the labels and {na}: valid at any number of labels, and wider than the "
                 "usual one would be once there are enough")
    if any(x["est"]["theta"] is not None and not x["est"]["reliable"] for x in gs):
        L.append(f"⚠ fewer than {DRAWN_EACH_SIDE} rows drawn by weight with or without the answer: the interval may be "
                 "too narrow")
    if any(x["est"]["theta"] is None for x in gs):
        L.append("– : too few labels drawn by weight in that group to estimate it (fewer than 2, or all the same)")
    t = r["totals"]
    if t is not None:
        words = {"met": "nothing more: the target is met", "pilot": "pilot: labels drawn by weight first",
                 "learn": f"labels, {nv} judging them too", "exact": "label every row: exact",
                 "active": f"labels and {na} alone", "every row": f"labels, {nv} on every row",
                 "mix": f"labels, {nv} on a run"}
        L += ["", f"the cheapest mix for ±{100 * r['target']:.1f} points" + (" in every group" if r["by"] else "")
              + f" (a label ${r['label_cost']:,.2f}; per row {nv} " + ("free" if not v["cost"] else f"${v['cost']:.6f}")
              + f", {na} already on every row):",
              f"{'group':<18} {'plan':<38} {'labels':>8} {nv + ' rows':>10} {'still costs':>12}"]
        for x in r["groups"]:
            if x["plan"]:
                p = x["plan"][0]
                L.append(f"{name(x['g']):<18.18} {words[p['kind']]:<38.38} {p['labels']:>8,} {p['rows']:>10,} "
                         f"{'$' + format(p['cost'], ',.2f'):>12}")
        steps = ("met", "pilot", "learn", "exact")
        chosen = any(x["plan"] and x["plan"][0]["kind"] not in steps for x in r["groups"])
        for way, tag in (("cheapest", "in all"), ("active", f"  labels and {na} alone (estimate --weight)"),
                         ("every row", f"  labels, {nv} on every row")):
            if way in t and (way == "cheapest" or chosen):
                L.append(f"{tag:<57.57} {t[way]['labels']:>8,} {t[way]['rows']:>10,} "
                         f"{'$' + format(t[way]['cost'], ',.2f'):>12}")
        best = t.get("cheapest")
        if best:
            first = [x["plan"][0] for x in r["groups"] if x["plan"]]
            now = [this_round(p) for p in first]
            n0, n1 = sum(p["more_labels"] for p in now), sum(p["more_rows"] for p in now)
            part = n0 < best["more_labels"]
            steps = [f"{n0:,} more label{'s' * (n0 != 1)} drawn by weight" + (" now" if part else "")] * bool(n0) + [
                f"{n1:,} more row{'s' * (n1 != 1)} judged by {nv}"] * bool(n1)
            L.append("cheapest: " + (", ".join(steps) if steps else "nothing more; the target is met")
                     + ("; then plan again" if part or any(p["kind"] in ("pilot", "learn") for p in first) else ""))
    return "\n".join(L)
