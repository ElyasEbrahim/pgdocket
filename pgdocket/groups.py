"""Shares by group (`pgdocket estimate --by COL`) and weighted shares (`--weight EXPR`): how far each figure can be
trusted, and where the next labels should go (#25, #26).

A GROUP BY result is only as good as its least reliable group, and two things decide that, neither of them the
model's confidence:
- **Systematic error.** A model can be wrong more often in some groups than in others (two products confused more in
  one region). One correction fitted on all the labels moves every group by the same amount and is biased wherever the
  model is wrong more or less often than on average (Mozer, 2026). So each group is corrected with its own labels:
  prediction-powered inference (PPI++) within the group, which is unbiased whatever the model gets wrong there.
- **Where the labels fall.** A group's interval narrows with *its* labels. Labels drawn at random across the table
  arrive in proportion to group size, so the smallest group decides how many are needed. Giving each group the labels
  its own target needs, given how often the model disagrees with people there, costs far less: a Neyman allocation, as
  in stratified PPI (Fisch et al., 2024) and in allocating human oversight across segments (Ye, Lyu & Tao, 2026).

Labels drawn within groups (`--draw`, imported with `label --stratified COL`) are a uniform sample of their group, so
they count for that column's groups only: estimates of the whole table, calibration and drift keep to random labels.

**Weights (#26).** A query that sums a column, or counts a row once for every row a join pairs it with, gives each row
a weight, and its share is Σ w·y / Σ w. A row's errors then count by its weight: a row a join uses a thousand times
carries a thousand rows' worth of the same error, since it was judged once. Labels drawn at random rarely include such a
row, and when they don't, neither the estimate nor its variance sees it. Two estimates follow:
- From labels drawn at random: rows heavier than every unlabelled row are known exactly (any label: no sample stands
  in for them) and the rest is PPI++ on weighted means (a Hájek estimator). It is trusted only while no unlabelled row
  holds more of the weight than the interval's half-width, since that row alone could move the share further.
- From labels drawn by weight (`--target --draw`), the design a join calls for: sampling the query's rows rather than
  the table's (Chaudhuri, Motwani & Narasayya, 1999), i.e. drawing rows with probability in proportion to weight
  (Hansen & Hurwitz, 1943). It is sequential Poisson sampling (Ohlsson, 1998): each row has a permanent random number u
  (from its key), rows are ranked by u / w, and the first ones are labelled. Rows heavy enough are certain to be drawn
  and count exactly; the rest, drawn in proportion to weight, estimate the remainder by the mean of their residuals.
  More labels extend the same ranking, so rounds nest, and the sample is read back from the ledger (the labelled run at
  the top of the ranking) with no record of the draw, valid for the data as it is now.
With every weight 1 the estimate from random labels is the unweighted one.
"""
import bisect
import csv
import hashlib
import math
import os

from psycopg import sql

from .estimate import MIN_EACH_SIDE, norm_ppf

PILOT = 30            # labels a group gets before its own disagreement rate is trusted for planning
ALPHA = 0.05          # family-wise level of the systematic-error flags
ALL = "all rows"      # the one group of a weighted share without --by
HEAVY_CAP = 5000      # heaviest rows per group a weighted plan looks at
DRAWN_EACH_SIDE = 5   # rows drawn by weight with and without the answer an interval from them needs (#26: from 5,
                      # every cell of the CFPB fan-out held ≥ 93 %; with labels drawn at random the floor stays 10)


def ppi_weighted(s):
    """PPI++ for a weighted share Σ w·y / Σ w, from sums over the rows of `s` not known exactly: the n with a label
    drawn at random (k of them with the answer; Σw, Σw², Σw·y, Σw²·y, Σw·f, Σw²·f, Σw²·y·f) and the nu others (Σw,
    Σw², Σw·f, Σw²·f). Means are weighted (a Hájek estimator) and variances count each row by its weight relative to
    the mean weight of its set, so with every weight 1 these are the formulas of `estimate.ppi_share` and
    docket.estimate(). With every row labelled the share is exact. None when the labels can't estimate it (fewer
    than 2, or all the same)."""
    n, nu = s["n"], s["nu"]
    if nu == 0 and n:
        return {"theta": s["swy"] / s["sw"], "se": 0.0, "lam": 0.0, "res_var": 0.0, "exact": True}
    if n < 2 or s["k"] in (0, n):
        return None

    def spread(sw, sw2, sx, sw2x, rows):      # weighted mean of a 0/1 x, and Σ u² (x − mean)² / (rows − 1)
        m = sx / sw
        return m, (sw2x * (1 - 2 * m) + m * m * sw2) / ((rows - 1) * (sw / rows) ** 2)

    ybar, var_y = spread(s["sw"], s["sw2"], s["swy"], s["sw2y"], n)
    fl, var_fl = spread(s["sw"], s["sw2"], s["swf"], s["sw2f"], n)
    cov = (s["sw2yf"] - fl * s["sw2y"] - ybar * s["sw2f"] + ybar * fl * s["sw2"]) / ((n - 1) * (s["sw"] / n) ** 2)
    if nu < 2:
        return {"theta": ybar, "se": math.sqrt(var_y / n), "lam": 0.0, "res_var": var_y, "exact": False}
    fu, var_fu = spread(s["swu"], s["sw2u"], s["swfu"], s["sw2fu"], nu)
    _, var_f = spread(s["sw"] + s["swu"], s["sw2"] + s["sw2u"], s["swf"] + s["swfu"], s["sw2f"] + s["sw2fu"], n + nu)
    lam = 0.0 if var_f <= 0 else min(1.0, max(0.0, cov / ((1 + n / nu) * var_f)))
    res = max(0.0, var_y + lam * lam * var_fl - 2 * lam * cov)        # per label: what the model leaves unexplained
    return {"theta": ybar + lam * (fu - fl), "se": math.sqrt(res / n + lam * lam * max(0.0, var_fu) / nu), "lam": lam,
            "res_var": res, "exact": False}


def ppi_binary(n, k, sf_l, syf, nu, sf_u):
    """`ppi_weighted` with every weight 1, from counts of 0/1 answers: n labelled rows, k labelled with the answer,
    sf_l of them answered with it by the model and syf by both; nu unlabelled rows, sf_u answered with it."""
    return ppi_weighted({"n": n, "k": k, "sw": n, "sw2": n, "swy": k, "sw2y": k, "swf": sf_l, "sw2f": sf_l,
                         "sw2yf": syf, "nu": nu, "swu": nu, "sw2u": nu, "swfu": sf_u, "sw2fu": sf_u})


def effective(s):
    """Labelled rows with and without the answer, each side counted by its weights: (Σw)² / Σw² (Kish's effective
    sample size). Plain counts when every weight is 1."""
    no_w, no_w2 = s["sw"] - s["swy"], s["sw2"] - s["sw2y"]
    return (s["swy"] ** 2 / s["sw2y"] if s["sw2y"] > 0 else 0.0), (no_w ** 2 / no_w2 if no_w2 > 0 else 0.0)


def estimate_group(s):
    """A group's share from labels drawn at random: rows heavier than every unlabelled row exactly, the rest by
    `ppi_weighted`."""
    if not s["judged"]:
        return None
    if not s["n"] + s["nu"]:
        return {"theta": s["c_wy"] / s["weight"], "se": 0.0, "lam": 0.0, "res_var": 0.0, "exact": True}
    r = ppi_weighted(s)
    if r is None:
        return None
    a = (s["sw"] + s["swu"]) / s["weight"]                 # the rest's share of the weight
    return {"theta": s["c_wy"] / s["weight"] + a * r["theta"], "se": a * r["se"], "lam": r["lam"],
            "res_var": a * a * r["res_var"], "exact": r["exact"]}


def prn(key, seed="1"):
    """A row's permanent random number for draws by weight, uniform on (0, 1): md5(key || ':w:' || seed), as `_scope`
    computes it in SQL."""
    return (int(hashlib.md5(f"{key}:w:{seed}".encode()).hexdigest()[:13], 16) + 0.5) / 2 ** 52


def sps_estimate(s):
    """A group's weighted share from labels drawn by weight (sequential Poisson sampling, Ohlsson 1998). Rows are
    ranked by u / w; the sample is the run of labelled rows at the top, up to the first unlabelled row, at u / w = ξ.
    Rows with ξ·w ≥ 1 were certain to be drawn and count exactly. The others were drawn with probability ξ·w, in
    proportion to weight, so the plain mean of their residuals y − λ·f estimates the rest's weighted mean (a Hájek
    estimator, in which ξ cancels), and the model's weighted share of the rest is known: PPI++ with λ the regression
    slope of y on f in the sample. Variance as for draws with replacement, which errs wide."""
    if not s["judged"]:
        return None
    W, WR = s["weight"], s["p_wr"]
    if WR <= 0:
        return {"theta": s["p_cwy"] / W, "se": 0.0, "lam": 0.0, "res_var": 0.0, "exact": True}
    n, k = s["p_n"], s["p_k"]
    if n < 2 or k in (0, n):
        return None

    def moments(n, k, nf, nyf):                            # sample variances of y and f and their covariance
        ym, fm = k / n, nf / n
        return ym, fm, n * ym * (1 - ym) / (n - 1), n * fm * (1 - fm) / (n - 1), (nyf - n * ym * fm) / (n - 1)

    ym, fm, var_y, var_f, cov = moments(n, k, s["p_f"], s["p_yf"])
    lam = 0.0 if var_f <= 0 else min(1.0, max(0.0, cov / var_f))
    # the variance with half a disagreement added each way (a Jeffreys prior on the 2 × 2 table): a model that
    # agreed with every row drawn would otherwise claim no error at all
    _, _, vy, vf, cv = moments(n + 1, k + 0.5, s["p_f"] + 0.5, s["p_yf"])
    res = max(0.0, vy + lam * lam * vf - 2 * lam * cv)
    a = WR / W
    return {"theta": s["p_cwy"] / W + a * (lam * s["p_wrf"] / WR + ym - lam * fm), "se": a * math.sqrt(res / n),
            "lam": lam, "res_var": a * a * res, "exact": False}


def sums(g, w, f, y, labelled, uniform, u=None):
    """The statistics `group_stats` returns for one group, from arrays over its judged rows: the weight, the model's
    0/1 answer, the 0/1 label (anything where there is none), whether a row has a label of any kind, whether that label
    was drawn at random, and (weighted) each row's permanent random number. For the harness and tests."""
    import numpy as np
    w, f, y = (np.asarray(x, float) for x in (w, f, y))
    labelled = np.asarray(labelled, bool)
    uniform = np.asarray(uniform, bool) & labelled
    candidates = int((~uniform).sum())                     # rows a draw at random may pick, whatever their weight
    keep = w > 0
    w, f, labelled, uniform = w[keep], f[keep], labelled[keep], uniform[keep]
    y = np.where(labelled, y[keep], 0.0)
    cut = float(w[~labelled].max()) if (~labelled).any() else None
    x = np.ones(len(w), bool) if cut is None else w > cut
    L, U = ~x & uniform, ~x & ~uniform
    S = lambda m, v: float(v[m].sum())
    out = {"g": g, "judged": int(len(w)), "weight": float(w.sum()), "model": float((w * f).sum()), "w_cut": cut,
           "candidates": candidates, "c_n": int(x.sum()), "c_w": S(x, w), "c_wy": S(x, w * y),
           "n": int(L.sum()), "k": int(y[L].sum()), "sw": S(L, w), "sw2": S(L, w * w), "swy": S(L, w * y),
           "sw2y": S(L, w * w * y), "swf": S(L, w * f), "sw2f": S(L, w * w * f), "sw2yf": S(L, w * w * y * f),
           "nu": int(U.sum()), "swu": S(U, w), "sw2u": S(U, w * w), "swfu": S(U, w * f), "sw2fu": S(U, w * w * f),
           "none": int((~x & ~labelled).sum())}
    if u is not None:
        r = np.asarray(u, float)[keep] / w
        xi = float(r[~labelled].min()) if (~labelled).any() else math.inf
        C = (r < xi) & (xi * w >= 1)
        P, R = (r < xi) & ~C, ~C
        out.update(p_xi=None if math.isinf(xi) else xi, p_cn=int(C.sum()), p_cw=S(C, w), p_cwy=S(C, w * y),
                   p_n=int(P.sum()), p_k=int(y[P].sum()), p_f=int(f[P].sum()), p_yf=int((f * y)[P].sum()),
                   p_wr=S(R, w), p_wrf=S(R, w * f))
    return out


def summarise(stats, z):
    """Per-group estimates from `group_stats` (or `sums`) rows. A weighted group with a run of at least PILOT labels
    drawn by weight is estimated from them (`sps_estimate`), otherwise from labels drawn at random."""
    out = []
    for s in stats:
        g = dict(s)
        by_weight = g.get("p_n") is not None and g["p_cn"] + g["p_n"] >= PILOT
        est = sps_estimate(g) if by_weight else estimate_group(g)
        g["method"], g["est"] = ("weight" if by_weight else "random"), est
        g["model_share"] = g["model"] / g["weight"] if g["judged"] else None
        if by_weight:
            g["labels"], g["exact_share"] = g["p_cn"] + g["p_n"], g["p_cw"] / g["weight"]
            g["effective"], g["heaviest"] = (g["p_k"], g["p_n"] - g["p_k"]), 0.0
        else:
            g["labels"] = g["c_n"] + g["n"]                # rows whose labels the estimate uses
            g["exact_share"] = g["c_w"] / g["weight"] if g["judged"] else 0.0
            g["effective"] = effective(g) if g["n"] else (0.0, 0.0)
            # the most of the weight one unlabelled row holds: it can move the share that far on its own
            g["heaviest"] = g["w_cut"] / g["weight"] if g["judged"] and g["w_cut"] is not None else 0.0
        floor = DRAWN_EACH_SIDE if by_weight else MIN_EACH_SIDE
        g["reliable"] = bool(est) and (est["exact"] or (min(g["effective"]) >= floor and g["heaviest"] <= z * est["se"]))
        if est:
            g["ci"] = (max(0.0, est["theta"] - z * est["se"]), min(1.0, est["theta"] + z * est["se"]))
        out.append(g)
    return out


def combine(groups, z):
    """The whole scope as the groups' estimates weighted by their weight (judged rows, unweighted): a stratified
    estimate. None unless every group with judged rows has an estimate."""
    live = [g for g in groups if g["judged"]]
    if not live or any(g["est"] is None for g in live):
        return None
    total = sum(g["weight"] for g in live)
    theta = sum(g["weight"] / total * g["est"]["theta"] for g in live)
    se = math.sqrt(sum((g["weight"] / total * g["est"]["se"]) ** 2 for g in live))
    return {"theta": theta, "se": se, "ci": (max(0.0, theta - z * se), min(1.0, theta + z * se)),
            "judged": sum(g["judged"] for g in live), "weight": total, "reliable": all(g["reliable"] for g in live)}


def systematic(groups, alpha=ALPHA):
    """Groups where the model's error differs from its error in the other groups: these are the groups one correction
    for all of them would get wrong. The model's share minus the estimate in each group is compared with the
    inverse-variance mean of the other groups' (a z-test, Bonferroni across the groups still in play). Step-down: the
    most extreme group is flagged and set aside before testing the rest, so one badly wrong group can't make a fair
    one look wrong by dragging the mean."""
    usable = [g for g in groups if g["reliable"] and g["est"]["se"] > 0]
    flags = []
    while len(usable) >= 2:
        crit = norm_ppf(1 - alpha / (2 * len(usable)))
        worst = None
        for g in usable:
            others = [h for h in usable if h is not g]
            w = [1 / h["est"]["se"] ** 2 for h in others]
            rest = sum(wi * (h["model_share"] - h["est"]["theta"]) for wi, h in zip(w, others)) / sum(w)
            b = g["model_share"] - g["est"]["theta"]
            z = (b - rest) / math.sqrt(g["est"]["se"] ** 2 + 1 / sum(w))
            if worst is None or abs(z) > abs(worst["z"]):
                worst = {"group": g["g"], "bias": b, "pooled": rest, "z": z, "_g": g}
        if abs(worst["z"]) <= crit:
            break
        usable.remove(worst.pop("_g"))
        flags.append(worst)
    return flags


def plan(groups, target, z, top=None):
    """Labels each group needs for an interval of ± `target` (a share).

    Unweighted (`top` None): drawn at random within the group. A group with fewer than PILOT labels gets a pilot
    first: its disagreement rate isn't known yet. Otherwise it needs z² · (the per-label variance PPI++ leaves) /
    target² labels, and at least MIN_EACH_SIDE with and without the answer for the interval to hold; never more than
    its judged rows (then the share is exact). Also returns how many labels drawn at random across the whole scope
    would give every group as many, when every group is past its pilot.

    Weighted (`top`: per group, its heaviest rows as (weight, model says it), every row down to a weight `thr`, from
    `heaviest_rows`): drawn by weight, `draw` rows that are not certain from the top of the ranking by u / w
    (`_plan_weighted`). `more` is then the expected number of new labels, until `walk` takes the rows themselves."""
    rows = []
    for g in groups:
        if not g["judged"]:
            continue
        p = (_plan_group(g, target, z) if top is None else
             _plan_weighted(g, top.get(g["g"], {"thr": 0.0, "rows": []}), target, z))
        rows.append({"group": g["g"], "judged": g["judged"], "labels": g["labels"], **p})
    total_judged = sum(r["judged"] for r in rows)
    uniform = setter = None
    if top is None and rows and all(r["kind"] != "pilot" for r in rows):
        setter = max(rows, key=lambda r: r["need"] / r["judged"])          # the group random labels reach last
        uniform = min(total_judged, math.ceil(setter["need"] * total_judged / setter["judged"]))
    return {"target": target, "weighted": top is not None, "groups": rows, "more": sum(r["more"] for r in rows),
            "need": sum(r["need"] for r in rows), "uniform": uniform, "uniform_set_by": setter and setter["group"]}


def _plan_group(g, target, z):
    J, n, est = g["judged"], g["n"], g["est"]
    if est and est["exact"]:
        return {"need": g["labels"], "more": 0, "kind": "exact", "draw": 0}
    if n < PILOT:
        need = min(J, PILOT)
        kind = "exact" if need >= J else "pilot"
    else:
        # every label the same so far (a rare answer): about MIN_EACH_SIDE / p rows are needed to see it often enough
        p = 1 / (n + 2) if est is None else min(max(est["theta"], 1 / (n + 2)), 1 - 1 / (n + 2))
        width = 0 if est is None else math.ceil(z * z * est["res_var"] / target ** 2)
        enough = math.ceil(MIN_EACH_SIDE / min(p, 1 - p))
        need = min(J, max(n, width, enough))
        kind = "exact" if need >= J else "target"
    more = g["none"] if kind == "exact" else need - n
    return {"need": need, "more": more, "kind": kind, "draw": g["candidates"] if kind == "exact" else more}


def _plan_weighted(g, top, target, z, steps=400):
    """How many rows drawn by weight (not certain) a weighted share needs for ± `target`: `draw`, which `walk` then
    takes from the top of the ranking by u / w.

    At ξ, rows with w ≥ 1/ξ are certain and the rest R is drawn with probability ξ·w: about ξ·Σ_R w rows. The
    residual d = y − λ·f of a row drawn in proportion to weight has variance Σ_R (w / Σ_R w) · E[(d − d̄)² | f], from
    how often people disagree with the model on rows it gives each answer (smoothed rates, measured on the rows drawn
    by weight, which weight them as the estimate does), scaled up when the sample shows more variance than they imply.
    The share's variance is (Σ_R w / Σ w)² times that, over the rows drawn, and at least DRAWN_EACH_SIDE of them with
    and without the answer are expected: the smallest ξ whose rows drawn reach both sets `draw`. A group short of the
    target always asks for at least a quarter more than it has, and at most four times as many: rates
    measured on a small sample, a rare answer's above all, can ask for far too many. Before a pilot's worth of rows
    drawn by weight, the rates are unknown: the pilot is PILOT of them (and the certain rows). When nothing short of
    labelling every row will do, every row is labelled."""
    W, M, est = g["weight"], g["model"], g["est"]
    if est and (est["exact"] or (g["reliable"] and z * est["se"] <= target)):
        return {"need": g["labels"], "more": 0, "kind": "exact" if est["exact"] else "target", "draw": 0, "certain": 0}
    n = g["p_n"]
    pilot = n < PILOT
    if not pilot:
        q1 = (g["p_yf"] + 0.5) / (g["p_f"] + 1)            # P(people say it | the model said it), smoothed
        q0 = (g["p_k"] - g["p_yf"] + 0.5) / (n - g["p_f"] + 1)
        lam = est["lam"] if est else 1.0
    neg, cw, cwf = [], [0.0], [0.0]                       # `top` is heaviest first
    for w, f in top["rows"]:
        neg.append(-w)
        cw.append(cw[-1] + w)
        cwf.append(cwf[-1] + w * f)

    def at(xi):                                            # certain rows, and the rest's weight and model weight
        j = bisect.bisect_right(neg, -1 / xi)
        return j, W - cw[j], M - cwf[j]

    def variance(WR, WRf):                                 # per row drawn: Var of d, as a row drawn by weight has it
        s1 = WRf / WR
        d = s1 * (q1 - lam) + (1 - s1) * q0
        e1 = q1 * (1 - lam - d) ** 2 + (1 - q1) * (lam + d) ** 2
        e0 = q0 * (1 - d) ** 2 + (1 - q0) * d ** 2
        return s1 * e1 + (1 - s1) * e0, s1 * q1 + (1 - s1) * q0

    inflate = 1.0
    if not pilot and est and g["p_xi"]:
        v0, _ = variance(g["p_wr"], g["p_wrf"])
        inflate = max(1.0, (est["res_var"] / (g["p_wr"] / W) ** 2) / v0) if v0 > 0 else 1.0
    lo, rows = 1 / W, top["rows"]                         # at lo, about one row is drawn
    if rows and (len(rows) >= HEAVY_CAP or top["thr"] <= 0):
        hi = 1 / rows[-1][0]                               # beyond hi, rows `top` leaves out could be certain
    else:
        hi = 1 / top["thr"] if top["thr"] > 0 else lo
    if pilot:                                              # a pilot that fell short of PILOT rows goes on
        least, most = n + 1, math.inf
    else:                                                  # short of the target: a quarter more at least, 4 times at most
        least, most = 1.25 * n + 1, max(4 * n, n + 4 * PILOT)
    found = None
    for i in range(steps + 1):
        xi = lo * (hi / lo) ** (i / steps)
        j, WR, WRf = at(xi)
        if WR <= 1e-9 * W:
            break
        if pilot:
            needed = PILOT
        else:
            v, p1 = variance(WR, WRf)
            needed = max(z * z * (WR / W) ** 2 * v * inflate / target ** 2, DRAWN_EACH_SIDE / min(p1, 1 - p1))
        needed = min(max(needed, least), most)
        if xi * WR >= needed or i == steps:
            # past hi the rows `top` leaves out count as drawn rather than certain: an estimate of how many labels;
            # `walk` takes them from the ranking itself
            found = (j, math.ceil(needed))
            break
    if not found or found[0] + found[1] >= g["judged"]:
        return {"need": g["judged"], "more": g["judged"] - g["labels"], "kind": "exact", "draw": math.inf,
                "certain": g["judged"]}
    j, draw = found
    return {"need": j + draw, "more": max(0, j + draw - g["labels"]), "kind": "pilot" if pilot else "target",
            "draw": draw, "certain": j}


def walk(ranking, draw):
    """The rows a draw by weight takes: the shortest run at the top of the ranking (rows as (r, weight, labelled,
    key) in order of r = u / w) that holds `draw` rows that are not certain (ξ·w < 1, ξ the r of the next row).
    Returns the keys among them without a label, and whether the ranking ran out first."""
    ws = []
    for m, (r, w, _, _) in enumerate(ranking):
        if m and m - (len(ws) - bisect.bisect_left(ws, 1 / r)) >= draw:
            return [k for _, _, lab, k in ranking[:m] if not lab], False
        bisect.insort(ws, w)
    return [k for _, _, lab, k in ranking if not lab], True


# ---------------------------------------------------------------- database
def decision(conn, name):
    r = conn.execute("SELECT d.id, d.source, d.key_column, d.text_columns, v.spec FROM docket.decision d "
                     "JOIN docket.version v ON v.decision_id = d.id AND v.version = d.active_version "
                     "WHERE d.name = %s", (name,)).fetchone()
    if not r:
        raise SystemExit(f"no decision named {name!r}")
    return {"id": r[0], "source": r[1], "key": r[2], "text": r[3], "spec": r[4], "name": name}


def _cond(where):
    """The caller's SQL condition on the source table, as `estimate --where` takes it (% doubled for the driver)."""
    return sql.SQL(where.replace("%", "%%")) if where and where.strip() else sql.SQL("true")


def _scope(d, by, where, weight):
    """Judged rows in scope with their group, weight (`--weight`, an SQL expression on the source row `s`; 1 without),
    the model's 0/1 answer and the label's: `has_label` when the row has a label, `uni` when it was drawn at random from
    the table or within this column's groups; with a weight, `r` = u / w, u the row's permanent random number (`prn`).
    Parameters: ans, d, by, seed."""
    w = sql.SQL("(" + weight.replace("%", "%%") + ")::float8") if weight and weight.strip() else sql.SQL("1::float8")
    g = sql.SQL("s.{}::text").format(sql.Identifier(by)) if by else sql.SQL("{}::text").format(sql.Literal(ALL))
    u = (sql.SQL(", ((('x' || substr(md5(s.{key}::text || ':w:' || %(seed)s), 1, 13))::bit(52)::bigint)::float8 + 0.5)"
                 " / 4503599627370496.0 AS u").format(key=sql.Identifier(d["key"])) if weight else sql.SQL(""))
    return sql.SQL("""scope AS (
            SELECT {g} AS g, s.{key}::text AS k, (v.answer = %(ans)s)::int AS f, {w} AS w,
                   l.row_key IS NOT NULL AS has_label, (l.label = %(ans)s)::int AS y,
                   coalesce(l.sampling = 'random' OR (l.sampling = 'stratified' AND %(by)s = ANY(l.strata)), false) AS uni
                   {u}
            FROM {table} s JOIN {view} v ON v.key = s.{key}
                 LEFT JOIN docket.label l ON l.decision_id = %(d)s AND l.row_key = s.{key}::text
            WHERE v.status = 'judged' AND ({cond}))""").format(
        g=g, key=sql.Identifier(d["key"]), w=w, u=u, table=sql.SQL(d["source"]),
        view=sql.Identifier("docket", "v_" + d["name"]), cond=_cond(where))


_UNIFORM = (("sw", "w"), ("sw2", "w * w"), ("swy", "w * y"), ("sw2y", "w * w * y"), ("swf", "w * f"),
            ("sw2f", "w * w * f"), ("sw2yf", "w * w * y * f"))
_OTHER = (("swu", "w"), ("sw2u", "w * w"), ("swfu", "w * f"), ("sw2fu", "w * w * f"))
_BY_WEIGHT = (("p_cw", "w", "pc"), ("p_cwy", "w * y", "pc"), ("p_k", "y", "pp"), ("p_f", "f", "pp"),
              ("p_yf", "y * f", "pp"), ("p_wr", "w", "NOT pc"), ("p_wrf", "w * f", "NOT pc"))


def group_stats(conn, d, by, answer, where=None, weight=None, seed="1"):
    """One pass over the scope: per group, the sums `estimate_group` needs, and with a weight those of `sps_estimate`.
    Rows weighing 0 (or NULL) don't count. Labels count as a sample when drawn at random from the whole table or
    within this column's groups; any label counts for the rows heavier than every unlabelled row."""
    cols = [f"coalesce(sum(r.{e}) FILTER (WHERE NOT r.x AND r.uni), 0) AS {n}" for n, e in _UNIFORM]
    cols += [f"coalesce(sum(r.{e}) FILTER (WHERE NOT r.x AND NOT r.uni), 0) AS {n}" for n, e in _OTHER]
    if weight:
        cols += ["c.xi AS p_xi", "count(*) FILTER (WHERE r.pc) AS p_cn", "count(*) FILTER (WHERE r.pp) AS p_n"]
        cols += [f"coalesce(sum(r.{e}) FILTER (WHERE {m.replace('pc', 'r.pc').replace('pp', 'r.pp')}), 0) AS {n}"
                 for n, e, m in _BY_WEIGHT]
    q = sql.SQL("""
        WITH {scope},
        cut AS (
            SELECT g, max(w) FILTER (WHERE w > 0 AND NOT has_label) AS w_cut, count(*) FILTER (WHERE w < 0) AS negative,
                   count(*) FILTER (WHERE NOT uni) AS candidates {xi}
            FROM scope GROUP BY g
        ), r AS (
            SELECT sc.*, (c.w_cut IS NULL OR sc.w > c.w_cut) AS x {flags}
            FROM scope sc JOIN cut c ON c.g IS NOT DISTINCT FROM sc.g WHERE sc.w > 0
        )
        SELECT c.g, c.w_cut, c.negative, c.candidates, count(r.k) AS judged, coalesce(sum(r.w), 0) AS weight,
               coalesce(sum(r.w * r.f), 0) AS model,
               count(*) FILTER (WHERE r.x) AS c_n, coalesce(sum(r.w) FILTER (WHERE r.x), 0) AS c_w,
               coalesce(sum(r.w * r.y) FILTER (WHERE r.x), 0) AS c_wy,
               count(*) FILTER (WHERE NOT r.x AND r.uni) AS n, coalesce(sum(r.y) FILTER (WHERE NOT r.x AND r.uni), 0) AS k,
               count(*) FILTER (WHERE NOT r.x AND NOT r.uni) AS nu,
               count(*) FILTER (WHERE NOT r.x AND NOT r.has_label) AS none, {cols}
        FROM cut c LEFT JOIN r ON r.g IS NOT DISTINCT FROM c.g
        GROUP BY c.g, c.w_cut, c.negative, c.candidates{group_xi} ORDER BY c.g NULLS LAST""").format(
        scope=_scope(d, by, where, weight), cols=sql.SQL(", ".join(cols)),
        xi=sql.SQL(", min(u / w) FILTER (WHERE w > 0 AND NOT has_label) AS xi" if weight else ""),
        flags=sql.SQL(", (c.xi IS NULL OR sc.u / sc.w < c.xi) AND (c.xi IS NULL OR c.xi * sc.w >= 1) AS pc, "
                      "(c.xi IS NOT NULL AND sc.u / sc.w < c.xi AND c.xi * sc.w < 1) AS pp" if weight else ""),
        group_xi=sql.SQL(", c.xi" if weight else ""))
    cur = conn.execute(q, {"ans": answer, "d": d["id"], "by": by, "seed": str(seed)})
    names = [c.name for c in cur.description]
    floats = {"w_cut", "weight", "model", "c_w", "c_wy", "p_xi"} | {n for n, *_ in _UNIFORM + _OTHER + _BY_WEIGHT
                                                                       if n not in ("p_k", "p_f", "p_yf")}
    out = []
    for row in cur.fetchall():
        s = dict(zip(names, row))
        if s.pop("negative"):
            raise SystemExit(f"{d['name']}: --weight is negative on some rows of {s['g']}: weights must be ≥ 0")
        out.append({c: v if c == "g" or v is None else float(v) if c in floats else int(v) for c, v in s.items()})
    return out


def heaviest_rows(conn, d, by, answer, where, weight, groups, target, z, seed="1"):
    """Per group, its heaviest rows, heaviest first, as (weight, model says it): every row a weighted plan might make
    certain, down to thr = weight · target / (2 z √rows) (rows lighter are certain only when nearly every row is
    drawn), and at most HEAVY_CAP. {group: {"thr": thr, "rows": [...]}}."""
    want = [(g["g"], g["weight"] * target / (2 * z * math.sqrt(g["judged"]))) for g in groups if g["judged"]]
    out = {g: {"thr": thr, "rows": []} for g, thr in want}
    if not want:
        return out
    q = sql.SQL("""
        WITH want AS (SELECT * FROM unnest(%(g)s::text[], %(thr)s::float8[]) AS x(g, thr)),
        {scope},
        ranked AS (
            SELECT sc.g, sc.w, sc.f, row_number() OVER (PARTITION BY sc.g ORDER BY sc.w DESC, sc.k) AS rn
            FROM scope sc JOIN want x ON x.g IS NOT DISTINCT FROM sc.g WHERE sc.w > 0 AND sc.w >= x.thr
        )
        SELECT g, w, f FROM ranked WHERE rn <= %(cap)s ORDER BY g NULLS LAST, rn""").format(
        scope=_scope(d, by, where, weight))
    for g, w, f in conn.execute(q, {"g": [x[0] for x in want], "thr": [x[1] for x in want], "cap": HEAVY_CAP,
                                    "ans": answer, "d": d["id"], "by": by, "seed": str(seed)}):
        out[g]["rows"].append((float(w), int(f)))
    return out


def ranking(conn, d, by, answer, where, weight, limits, seed="1"):
    """The top of each group's ranking by u / w, as `walk` takes it: {group: [(r, weight, labelled, key)]}, at most
    limits[group] rows (all of them for ∞)."""
    want = [(g, None if math.isinf(m) else int(m)) for g, m in limits.items()]
    q = sql.SQL("""
        WITH want AS (SELECT * FROM unnest(%(g)s::text[], %(m)s::int[]) AS x(g, m)),
        {scope},
        ranked AS (
            SELECT sc.g, sc.k, sc.w, sc.has_label, sc.u / sc.w AS r,
                   row_number() OVER (PARTITION BY sc.g ORDER BY sc.u / sc.w, sc.k) AS rn
            FROM scope sc JOIN want x ON x.g IS NOT DISTINCT FROM sc.g WHERE sc.w > 0
        )
        SELECT x.g, r.r, r.w, r.has_label, r.k FROM ranked r JOIN want x ON x.g IS NOT DISTINCT FROM r.g
        WHERE x.m IS NULL OR r.rn <= x.m ORDER BY x.g NULLS LAST, r.rn""").format(scope=_scope(d, by, where, weight))
    out = {g: [] for g, _ in want}
    for g, r, w, any_, k in conn.execute(q, {"g": [g for g, _ in want], "m": [m for _, m in want], "ans": answer,
                                             "d": d["id"], "by": by, "seed": str(seed)}):
        out[g].append((float(r), float(w), bool(any_), k))
    return out


def _take(conn, d, by, answer, where, weight, plan_, seed):
    """For each group of a weighted plan, the rows to label (`walk` down its ranking), into `keys` and `more`."""
    todo = {r["group"]: r for r in plan_["groups"] if r["draw"]}
    limits = {g: math.inf if math.isinf(r["draw"]) else 2 * (r["certain"] + r["draw"]) + 100 for g, r in todo.items()}
    while todo:
        heads = ranking(conn, d, by, answer, where, weight, {g: limits[g] for g in todo}, seed)
        for g, rows in heads.items():
            keys, out = walk(rows, todo[g]["draw"])
            if out and len(rows) >= limits[g]:             # ran out of the rows fetched, not of the group's: fetch more
                limits[g] *= 4
                continue
            todo.pop(g).update(keys=keys, more=len(keys))
    plan_["more"] = sum(r["more"] for r in plan_["groups"])


def estimate_by(conn, name, by, answer=None, where=None, level=0.95, target=None, weight=None, seed="1"):
    d = decision(conn, name)
    if d["spec"]["kind"] == "noul":
        answer = "yes"
    else:
        names = [o["name"] for o in d["spec"]["options"]]
        if answer is None:
            raise SystemExit(f"estimate {'--by' if by else '--weight'} needs --answer: the option whose share to estimate")
        if answer not in names:
            raise SystemExit(f"{answer!r} is not an option of {name}: {', '.join(names)}")
    z = norm_ppf(0.5 + level / 2)
    groups = summarise(group_stats(conn, d, by, answer, where, weight, seed), z)
    p = None
    if target:
        top = heaviest_rows(conn, d, by, answer, where, weight, groups, target, z, seed) if weight else None
        p = plan(groups, target, z, top)
        if weight:
            _take(conn, d, by, answer, where, weight, p, seed)
    return {"name": name, "by": by, "answer": answer, "level": level, "where": where, "weight": weight, "seed": seed,
            "groups": groups, "overall": combine(groups, z) if by else None, "systematic": systematic(groups),
            "plan": p}


def draw(conn, name, by, plan_, where=None, seed="1"):
    """The rows to label next. A weighted plan carries them (`_take`: down each group's ranking by u / w). Otherwise,
    for each group, `draw` rows drawn at random among its judged rows without a label drawn at random (from the table,
    or within this column's groups). Returns (d, [(key, group, 'by weight' | 'at random')])."""
    d = decision(conn, name)
    if plan_["weighted"]:
        return d, [(k, r["group"], "by weight") for r in plan_["groups"] for k in r.get("keys", [])]
    want = [(r["group"], r["draw"]) for r in plan_["groups"] if r["draw"] > 0]
    if not want:
        return d, []
    q = sql.SQL("""
        WITH want AS (SELECT * FROM unnest(%(g)s::text[], %(m)s::int[]) AS w(g, m)),
        cand AS (
            SELECT s.{key}::text AS k, s.{by}::text AS g,
                   row_number() OVER (PARTITION BY s.{by}::text ORDER BY md5(s.{key}::text || %(seed)s)) AS rn
            FROM {table} s JOIN {view} v ON v.key = s.{key}
            WHERE v.status = 'judged' AND ({cond}) AND NOT EXISTS (
                SELECT 1 FROM docket.label l WHERE l.decision_id = %(d)s AND l.row_key = s.{key}::text
                  AND (l.sampling = 'random' OR (l.sampling = 'stratified' AND %(by)s = ANY(l.strata))))
        )
        SELECT c.k, c.g FROM cand c JOIN want w ON w.g IS NOT DISTINCT FROM c.g WHERE c.rn <= w.m
        ORDER BY c.g NULLS LAST, c.rn""").format(
        by=sql.Identifier(by), key=sql.Identifier(d["key"]), table=sql.SQL(d["source"]),
        view=sql.Identifier("docket", "v_" + name), cond=_cond(where))
    rows = conn.execute(q, {"g": [g for g, _ in want], "m": [m for _, m in want], "seed": f":{by}:{seed}",
                            "d": d["id"], "by": by}).fetchall()
    return d, [(k, g, "at random") for k, g in rows]


def export(conn, d, by, rows, path):
    """Write the drawn rows to label: key, group, how it was drawn, the text columns, an empty label. Blind: no AI
    answer."""
    keys = [k for k, _, _ in rows]
    q = sql.SQL("SELECT {k}::text, {cols} FROM {t} WHERE {k}::text = ANY(%s)").format(
        k=sql.Identifier(d["key"]), cols=sql.SQL(", ").join(sql.Identifier(c) for c in d["text"]), t=sql.SQL(d["source"]))
    text = {r[0]: r[1:] for r in conn.execute(q, (keys,)).fetchall()}
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["key"] + ([by] if by else []) + ["drawn"] + list(d["text"]) + ["label"])
        for k, g, how in rows:
            w.writerow([k] + (["" if g is None else g] if by else []) + [how]
                       + list(text.get(k, [""] * len(d["text"]))) + [""])
    return len(rows)


def render(r):
    """Plain-text table for the CLI."""
    pct = lambda x: "–" if x is None else f"{100 * x:.1f} %"
    lvl = f"{100 * r['level']:.0f} %"
    weighted = bool(r.get("weight"))
    L = [f"{r['name']}: share of judged rows whose true answer is {r['answer']!r}"
         + (f", each counted by {r['weight']}" if weighted else "") + (f", by {r['by']}" if r["by"] else "")
         + f" ({lvl} intervals)" + (f", where {r['where']}" if r["where"] else "")]
    tgt = r["plan"]
    head = f"{'group':<20} {'rows':>7} {'labels':>7}" + (f" {'drawn':>9} {'exact':>6}" if weighted else "")
    head += f" {'model says':>11} {'estimate':>9} {lvl + ' interval':>18}"
    if tgt:
        head += f"  {'labels for ±' + format(100 * tgt['target'], '.1f') + ' pts':>22}"
    L.append(head)
    plans = {p["group"]: p for p in (tgt["groups"] if tgt else [])}
    for g in r["groups"]:
        est = g["est"]
        ci = "exact" if est and est["exact"] else (f"{100 * g['ci'][0]:.1f} – {100 * g['ci'][1]:.1f}" if est else "–")
        line = (f"{str(g['g']) if g['g'] is not None else '(none)':<20.20} {g['judged']:>7,} {g['labels']:>7,}"
                + (f" {'by weight' if g['method'] == 'weight' else 'at random':>9} {100 * g['exact_share']:>4.0f} %"
                   if weighted else "")
                + f" {pct(g['model_share']):>11} {pct(est['theta'] if est else None):>9} {ci:>18}")
        if tgt and g["g"] in plans:
            p = plans[g["g"]]
            note = {"pilot": f"+{p['more']} (pilot)", "exact": f"+{p['more']} (all: exact)", "target": f"+{p['more']}"}
            line += f"  {note[p['kind']]:>22}"
        L.append(line + ("" if g["reliable"] or not est else "  ⚠"))
    o = r["overall"]
    if o and len(r["groups"]) > 1:
        L.append(f"{'all groups':<20} {o['judged']:>7,} {'':>7}" + (f" {'':>9} {'':>6}" if weighted else "")
                 + f" {'':>11} {pct(o['theta']):>9} {100 * o['ci'][0]:.1f} – {100 * o['ci'][1]:.1f}".rstrip())
    if weighted:
        L.append("drawn: how the labels the estimate uses were chosen; exact: the share of the weight in rows known "
                 "exactly from their labels")
    unsure = [g for g in r["groups"] if g["est"] and not g["reliable"]]
    if any(g["method"] == "random" and min(g["effective"]) < MIN_EACH_SIDE for g in unsure):
        L.append(f"⚠ fewer than {MIN_EACH_SIDE} labelled rows with or without the answer"
                 + (" (counted by weight)" if weighted else "") + ": the interval may be too narrow")
    if any(g["method"] == "weight" and min(g["effective"]) < DRAWN_EACH_SIDE for g in unsure):
        L.append(f"⚠ fewer than {DRAWN_EACH_SIDE} rows drawn by weight with or without the answer (rows certain to be "
                 "drawn aside): the interval may be too narrow")
    heavy = [g for g in unsure if g["method"] == "random" and min(g["effective"]) >= MIN_EACH_SIDE]
    if heavy:
        top = max(heavy, key=lambda g: g["heaviest"])
        L.append(f"⚠ an unlabelled row holds {100 * top['heaviest']:.1f} % of the weight"
                 + (f" in {top['g']}" if r["by"] else "") + ", more than the interval's half-width: labels drawn at "
                 "random can't speak for it. Draw by weight (--target --draw).")
    if any(g["est"] is None and g["judged"] for g in r["groups"]):
        L.append("– : too few labels in that group to estimate it (fewer than 2, or all the same)")
    for f in r["systematic"]:
        side = "overstates" if f["bias"] > 0 else "understates"
        L.append(f"! the model {side} {r['answer']!r} by {abs(100 * f['bias']):.1f} points in {f['group']} "
                 f"(by {100 * f['pooled']:+.1f} in the other groups, z = {f['z']:.1f}): its error differs by {r['by']}, so a "
                 "correction fitted on all labels would be off there. Each group is corrected with its own labels.")
    if tgt and tgt["weighted"]:
        certain = sum(p["certain"] for p in tgt["groups"] if p["kind"] in ("pilot", "target"))
        L.append(f"to reach ±{100 * tgt['target']:.1f} points" + (" in every group" if r["by"] else "")
                 + f": {tgt['more']:,} more labels, drawn by weight ({tgt['need']:,} in all"
                 + (f", {certain:,} of them rows heavy enough to be certain" if certain else "") + ")"
                 + (("; groups marked pilot need a first draw before the rest can be planned" if r["by"] else
                     "; a pilot first, the rest planned from it") if any(p["kind"] == "pilot" for p in tgt["groups"])
                    else ""))
    elif tgt:
        L.append(f"to reach ±{100 * tgt['target']:.1f} points in every group: {tgt['more']:,} more labels, drawn at random "
                 f"within each group ({tgt['need']:,} in all)" + (
                     f"; drawn at random across all groups instead, about {tgt['uniform']:,} would be needed "
                     f"(set by {tgt['uniform_set_by']}, which random labels reach last)"
                     if tgt["uniform"] else "; groups marked pilot need a first draw before the rest can be planned"))
    return "\n".join(L)
