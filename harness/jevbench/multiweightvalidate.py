"""Several models for a weighted share, on real answers (#29, no API calls).

The 2,000 CFPB narratives judged in Phase 0 stand for the 21,980 complaints in the source shard that carry them word
for word (#26): a join with fan-out, one text counted up to 2,866 times. The query is the share of these complaints
about X, to ± 2 points. Two setups, as pgdocket runs them: the active version judges every text, already paid for (Jev
at 20 rows per request, A; Laya, B), and Jev one row per request, the better model ($23.71 per million texts), judges a
run of the ranking by weight (`estimate --with 2 --weight copies`). Labels cost $1.

1. coverage on the real join: labels drawn by weight inside Jev's run (the first n0 and m1 texts of a ranking by u / w,
   u drawn afresh each time), against #26's estimate from the same labels with the active version alone, and with Jev
   on every text;
2. the cost of ± 2 points on tables of 1 million and 100 million texts with this fan-out (the 2,000 texts repeated, the
   models' correlations as measured): the cheapest mix, the labels with the active version alone (#26's design), and
   with Jev on every text;
3. plans carried out in rounds on 1 million texts (the 2,000 repeated 500 times, each copy with its own random number):
   a pilot, then the cheapest thresholds, planned again until met; against the same rounds without Jev.
"""
import json
import math
import os

import numpy as np

from pgdocket import groups as G
from pgdocket import multiweight as MW
from pgdocket.estimate import norm_ppf

from . import multivalidate as MV
from . import weightvalidate as WV

OUT = "results/multiweight-validation"
SETUPS = {"A": "Jev, 20 rows per request", "B": "Laya, 1 row per request"}
BETTER = "Jev, 1 row per request"
ANSWERS = WV.ANSWERS
TARGET = 0.02
LABEL = 1.0
DESIGNS = ((100, 500), (200, 1000))       # labels, and texts Jev judges, at the top of the ranking
TABLES = {500: "1 million", 50_000: "100 million"}   # copies of the 2,000 texts
ROUND_COPIES = 500
ROUNDS = 8
NAN = np.nan
Z = norm_ppf(0.975)


def load():
    data = WV.load()
    return {"truth": data["truth"], "w": data["weights"]["all complaints"], "models": data["models"],
            "cost": MV.load()["cost"]["Jev, 1 row per request"]}


def _held(lo_hi, truth):
    return lo_hi[0] <= truth <= lo_hi[1]


def coverage(w, y, a, v, trials, rng):
    """How often each interval holds the weighted share (draws whose own floor holds), and its mean half-width."""
    truth = float((w * y).sum() / w.sum())
    out = {}
    none = np.zeros(len(w), bool)
    for n0, m1 in DESIGNS:
        res = {k: [] for k in ("two-phase", "#26, active version", "#26, Jev on every text")}
        for _ in range(trials):
            u = rng.random(len(w))
            order = np.argsort(u / w)
            lab, run = np.zeros(len(w), bool), np.zeros(len(w), bool)
            lab[order[:n0]], run[order[:m1]] = True, True
            e = MW.estimate(MW.sums("all", w, a, np.where(run, v, NAN), np.where(lab, y, NAN), u), Z)
            if e and e["theta"] is not None and e["reliable"]:
                res["two-phase"].append((_held(e["ci"], truth), Z * e["se"], "v2" in e["used"][-1]))
            for key, f in (("#26, active version", a), ("#26, Jev on every text", v)):
                g = WV.estimate(w, f, y, lab, none, u)
                if g["est"] and g["reliable"] and g["method"] == "weight":
                    res[key].append((_held(g["ci"], truth), Z * g["est"]["se"], False))
        out[f"{n0} labels, Jev on {m1:,} texts"] = {
            k: {"coverage": float(np.mean([h for h, _, _ in x])), "half_width": float(np.mean([s for _, s, _ in x])),
                "draws": len(x), "of": trials, "used_jev": float(np.mean([j for _, _, j in x]))}
            for k, x in res.items() if x}
    return out


def population(w, y, a, v):
    """The variances the plan takes, in the population where a text appears as often as its weight: of y given the
    active version (s2a), given both (s2), and of the part of the prediction from both the active version leaves (s1)."""
    W = w.sum()

    def fit(X):
        Xw = X * w[:, None]
        b = np.linalg.lstsq(X.T @ Xw, Xw.T @ y, rcond=None)[0]
        return b, float((w * (y - X @ b) ** 2).sum() / W)

    one = np.ones(len(w))
    _, s2a = fit(np.c_[one, a])
    b, s2 = fit(np.c_[one, v, a])
    pa = {k: float((w * v)[a == k].sum() / w[a == k].sum()) if (a == k).any() else 0.0 for k in (0, 1)}
    s1 = float(b[1] ** 2 * (w * (v - np.where(a == 1, pa[1], pa[0])) ** 2).sum() / W)
    return {"s1": s1, "s2": s2, "s2a": s2a, "share": float((w * y).sum() / W)}


def _heavy(w, copies):
    """The heaviest rows of the table made of `copies` copies of each text, as `groups.heaviest_rows` caps them."""
    rows = []
    for x in sorted(w, reverse=True):
        rows += [(float(x), 0)] * copies
        if len(rows) >= G.HEAVY_CAP:
            break
    return {"thr": 0.0, "rows": rows[:G.HEAVY_CAP]}


def costs(w, pop, cost_v):
    """± TARGET from scratch on tables of the texts repeated: the cheapest mix, the labels with the active version
    alone, and with Jev on every text."""
    out = {}
    for copies, label in TABLES.items():
        s = {"weight": float(w.sum() * copies), "rows": len(w) * copies, "w2": float((w * w).sum() * copies),
             "xi0": 1e-15, "xi1": 1e-15}
        est = {"exact": False, "reliable": False, "se": 0.0, "drawn": 10 ** 6, "drawn_with": 3 * 10 ** 5,
               "share_R": pop["share"], "s1": pop["s1"], "s2": pop["s2"], "s2a": pop["s2a"]}
        ps = MW.plan(s, est, _heavy(w, copies), TARGET, Z, LABEL, cost_v)
        pick = lambda kind: next(p for p in ps if p["kind"] == kind)
        out[label] = {k: {"cost": p["cost"], "labels": p["labels"], "rows": p["rows"]}
                      for k, p in (("cheapest", ps[0]), ("active", pick("active")), ("every row", pick("every row")))}
    return out


def rounds(w, y, a, v, cost_v, runs, rng, with_v=True):
    """`estimate --with 2 --weight --target --queue --draw` round after round on the texts repeated ROUND_COPIES
    times (with_v False: the same plan without Jev): rounds, labels, Jev's texts, cost, whether the final interval held
    the truth, its half-width."""
    W, Y, A, Vv = (np.tile(x, ROUND_COPIES) for x in (w, y, a, v))
    truth = float((W * Y).sum() / W.sum())
    heavy = _heavy(w, ROUND_COPIES)
    out = []
    for _ in range(runs):
        u = rng.random(len(W))
        r = u / W
        x0 = x1 = 0.0
        for done in range(ROUNDS + 1):
            lab, run = r < x0, r < x1
            s = MW.sums("all", W, A, np.where(run, Vv, NAN), np.where(lab, Y, NAN), u)
            e = MW.estimate(s, Z)
            p = MW.plan(s, e, heavy, TARGET, Z, LABEL, cost_v, with_v=with_v)[0]
            if p["kind"] == "met" or done == ROUNDS:
                break
            step = MW.this_round(p)
            x0, x1 = max(x0, step["xi0"]), max(x1, step["xi1"] if with_v else 0.0)
        n_lab, n_run = int((r < x0).sum()), int((r < x1).sum())
        out.append({"rounds": done, "labels": n_lab, "rows": n_run, "cost": LABEL * n_lab + cost_v * n_run,
                    "held": bool(e["theta"] is not None and _held(e["ci"], truth)),
                    "half_width": Z * e["se"] if e["se"] is not None else None})
    hw = [o["half_width"] for o in out if o["half_width"] is not None]
    return {"runs": runs, "rounds": float(np.mean([o["rounds"] for o in out])),
            "labels": float(np.mean([o["labels"] for o in out])),
            "labels_p90": float(np.percentile([o["labels"] for o in out], 90)),
            "rows": float(np.mean([o["rows"] for o in out])), "cost": float(np.mean([o["cost"] for o in out])),
            "coverage": float(np.mean([o["held"] for o in out])), "half_width": float(np.mean(hw)),
            "within": float(np.mean(np.array(hw) <= TARGET * 1.05))}


def run(trials=1000, runs=100, seed=0):
    data = load()
    rng = np.random.default_rng(seed)
    w = data["w"]
    keep = w > 0
    w = w[keep]
    res = {"texts": int(len(w)), "copies": int(w.sum()), "max": int(w.max()), "kish": float(w.sum() ** 2 / (w * w).sum()),
           "cost": data["cost"], "trials": trials, "runs": runs, "target": TARGET, "setups": {}}
    v_all = {ans: (data["models"][BETTER][keep] == ans).astype(float) for ans in ANSWERS}
    for key, active in SETUPS.items():
        S = res["setups"][key] = {"active": active, "answers": {}}
        for ans in ANSWERS:
            y = (data["truth"][keep] == ans).astype(float)
            a = (data["models"][active][keep] == ans).astype(float)
            v = v_all[ans]
            pop = population(w, y, a, v)
            S["answers"][ans] = {
                "truth": pop["share"], "active_says": float((w * a).sum() / w.sum()),
                "jev_says": float((w * v).sum() / w.sum()), "population": pop,
                "coverage": coverage(w, y, a, v, trials, rng),
                "costs": costs(w, pop, data["cost"]),
                "rounds": {"with": rounds(w, y, a, v, data["cost"], runs, rng),
                           "active": rounds(w, y, a, v, data["cost"], runs, rng, with_v=False)}}
            print(key, ans, "done", flush=True)
    os.makedirs(OUT, exist_ok=True)
    with open(f"{OUT}/results.json", "w") as fh:
        json.dump(res, fh, indent=2, default=float)
    with open(f"{OUT}/report.md", "w") as fh:
        fh.write(render(res))
    return OUT


def render(res):
    pct = lambda x: f"{100 * x:.1f} %"
    usd = lambda x: f"${x:,.0f}" if x >= 100 else f"${x:,.2f}"
    L = ["# Several models for a weighted share, on real answers", "",
         f"No API calls. Generated by `jevbench multiweightvalidate`. {res['texts']:,} CFPB complaint texts judged once, "
         f"standing for the {res['copies']:,} complaints in the source shard that carry them word for word (the "
         f"heaviest {res['max']:,} times; Kish effective size {res['kish']:.0f}). The share of these complaints about "
         f"each product is wanted to ± {100 * res['target']:.0f} points (95 %). The active version judges every text "
         "(setup A: Jev at 20 rows per request; B: Laya), and `--with` adds Jev one row per request, at "
         f"${res['cost'] * 1e6:.2f} per million texts, on a run of the ranking by weight. Labels cost $1.", "",
         f"Coverage: {res['trials']} draws per cell, a fresh ranking each time; counted where the estimate's own floor "
         f"holds. Rounds: {res['runs']} runs per cell on the texts repeated {ROUND_COPIES} times.", ""]
    names = {"two-phase": "labels, active version, Jev on its run (`--with --weight`)",
             "#26, active version": "labels and the active version (#26)",
             "#26, Jev on every text": "labels and Jev on every text (#26)"}
    for key, S in res["setups"].items():
        L += [f"## Setup {key}: the active version is {S['active']}", ""]
        for ans, a in S["answers"].items():
            p = a["population"]
            L += [f"### {ans.capitalize()}: {pct(a['truth'])} of the complaints", "",
                  f"The active version says {pct(a['active_says'])}, Jev {pct(a['jev_says'])}. Residual variance per "
                  f"labelled row drawn by weight: {p['s2a']:.3f} given the active version, {p['s2']:.3f} given both.", "",
                  "**1. Coverage** (intervals holding the truth; mean half-width in points).", "",
                  "| design | " + " | ".join(names.values()) + " |", "| --- | ---: | ---: | ---: |"]
            for d, c in a["coverage"].items():
                cell = lambda k: (f"{pct(c[k]['coverage'])} ±{100 * c[k]['half_width']:.1f}"
                                  + ("" if c[k]["draws"] >= 0.9 * c[k]["of"] else f" ({c[k]['draws']} draws)")
                                  if k in c else "–")
                L.append(f"| {d} | " + " | ".join(cell(k) for k in names) + " |")
            L += ["", f"**2. Cost of ± {100 * res['target']:.0f} points** (labels; texts Jev judges; cost).", "",
                  "| texts | the cheapest mix | labels and the active version | labels and Jev on every text |",
                  "| ---: | ---: | ---: | ---: |"]
            for t, c in a["costs"].items():
                cell = lambda x: f"{usd(x['cost'])} ({x['labels']:,}" + (f"; {x['rows']:,}" if x["rows"] else "") + ")"
                L.append(f"| {t} | {cell(c['cheapest'])} | {cell(c['active'])} | {cell(c['every row'])} |")
            L += ["", f"**3. In rounds on {len(TABLES) and 2_000 * ROUND_COPIES:,} texts** (a pilot, then the cheapest "
                      "thresholds, until met).", "",
                  "| | rounds | labels (p90) | texts Jev judged | cost | intervals held | half-width | within 5 % of target |",
                  "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
            for k, tag in (("with", "`--with 2 --weight`"), ("active", "the active version alone")):
                rr = a["rounds"][k]
                L.append(f"| {tag} | {rr['rounds']:.1f} | {rr['labels']:,.0f} ({rr['labels_p90']:,.0f}) | "
                         f"{rr['rows']:,.0f} | {usd(rr['cost'])} | {pct(rr['coverage'])} | {100 * rr['half_width']:.2f} | "
                         f"{pct(rr['within'])} |")
            L.append("")
    return "\n".join(L) + "\n"
