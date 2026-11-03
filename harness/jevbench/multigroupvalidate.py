"""Several models and labels, region by region, on real answers (#28, no API calls).

The 1,992 CFPB complaints from the four US Census regions (Midwest 252, Northeast 336, South 984, West 420; the 8 from
elsewhere are left out) carry the product the consumer chose (the labels) and three stored sets of answers. The query
is "share of complaints about X, by region", each region to ± 3 points, as `estimate --with 2 --by region --target 0.03`
plans it. Two setups, as pgdocket runs them: the active version answers every row of the column (already paid for, so
free to the estimate), and Jev one row per request, the better model ($23.70 per million rows), judges a sample in each
region:

  A. the active version is Jev at 20 rows per request ($13.20 per million rows, already paid);
  B. the active version is Laya (no API cost, a weaker model).

1. coverage on 200,000 complaints resampled region by region from the 1,992 (each region ×100): labels drawn within
   each region, inside a sample of Jev's answers; against each region from its labels and the active version alone
   (`estimate --by`), and from its labels alone;
2. the cost of ± 3 points in every region, labels at $1, on tables of 1 million and 100 million rows in these
   proportions: each region's cheapest mix; the active version alone in each region (`estimate --by`); labels alone;
   one design drawn across the table, which must bring the smallest region to ± 3 (`--with` without `--by`); and, for
   scale, ± 3 points on the whole table;
3. the plan carried out in rounds on 100 million rows, as `--target --queue --draw` would: a pilot of 30 labels in each
   region, then each region's cheapest mix from what its labels showed, planned again until met. Labelled and sampled
   rows are draws from the region's complaints; the active version's answers on the rest count at their mean.
"""
import json
import math
import os

import numpy as np

from pgdocket import multi as M
from pgdocket.estimate import MIN_EACH_SIDE, norm_ppf

from . import groupvalidate as GV
from . import multivalidate as MV

OUT = "results/multigroup-validation"
REGIONS = ("Midwest", "Northeast", "South", "West")
SETUPS = {"A": "Jev, 20 rows per request", "B": "Laya, 1 row per request"}    # the active version, on every row
BETTER = "Jev, 1 row per request"                                            # --with: judges a sample
ANSWERS = ("credit reporting", "debt collection", "credit card", "mortgage")
TARGET = 0.03
TABLES = (1_000_000, 100_000_000)
LABEL = 1.0
COPIES = 100                                  # the resampled population: each region's complaints ×100
DESIGNS = ((100, 2000), (300, 10000))         # labels in each region, inside a sample of the better model's answers
ROUNDS = 6
NAN = np.nan
Z = norm_ppf(0.975)


def load():
    data = GV.load()
    keep = np.isin(data["region"], REGIONS)
    cost = MV.load()["cost"]
    return {"truth": data["truth"][keep], "region": data["region"][keep],
            "models": {n: a[keep] for n, a in data["models"].items()},
            "cost": {"Jev, 1 row per request": cost["Jev, 1 row per request"],
                     "Jev, 20 rows per request": cost["Jev, 20 rows per request"], "Laya, 1 row per request": 0.0}}


def _versions(active, w):
    return [{"version": 1, "model": active, "status": "active", "cost": 0.0},
            {"version": 2, "model": BETTER, "status": "shadow", "cost": w}]


def coverage(y, f1, f2, region, trials, rng):
    """Labels drawn within each region inside a sample of the better model (v2), the active version (v1) on every
    row: how often each region's interval holds its share, and the regions together."""
    pop = {g: rng.choice(np.where(region == g)[0], COPIES * int((region == g).sum())) for g in REGIONS}
    N = sum(len(i) for i in pop.values())
    truth = {g: float(y[i].mean()) for g, i in pop.items()}
    whole = sum(len(pop[g]) * truth[g] for g in REGIONS) / N
    out = {}
    for n0, m in DESIGNS:
        res = {k: {g: [] for g in REGIONS + ("all regions",)} for k in ("with", "active", "labels")}
        for _ in range(trials):
            ests = {k: {} for k in res}
            for g, idx in pop.items():
                Ng = len(idx)
                order = rng.permutation(Ng)
                lab, sample = np.zeros(Ng, bool), np.zeros(Ng, bool)
                lab[order[:n0]], sample[order[:m]] = True, True
                st = M.pattern_stats(["labels", "v1", "v2"],
                                     [np.where(lab, y[idx], NAN), f1[idx], np.where(sample, f2[idx], NAN)])
                for k, keep in (("with", [0, 1, 2]), ("active", [0, 1]), ("labels", [0])):
                    e = M.estimate(M._restrict(st, keep), Z, Ng)
                    ests[k][g] = e
                    if e["theta"] is not None and e["reliable"]:
                        res[k][g].append((abs(e["theta"] - truth[g]) <= Z * e["se"], Z * e["se"]))
            for k, es in ests.items():
                if all(e["theta"] is not None and e["reliable"] for e in es.values()):
                    theta = sum(len(pop[g]) / N * es[g]["theta"] for g in REGIONS)
                    se = math.sqrt(sum((len(pop[g]) / N * es[g]["se"]) ** 2 for g in REGIONS))
                    res[k]["all regions"].append((abs(theta - whole) <= Z * se, Z * se))
        out[f"{n0} labels in each region, {m:,} judged by the better model"] = {
            k: {g: {"coverage": float(np.mean([h for h, _ in v])), "half_width": float(np.mean([w for _, w in v])),
                    "draws": len(v), "of": trials} for g, v in by.items() if v} for k, by in res.items()}
    return out


def _var(share, rho, m, N):
    """MFMC's variance with labels m[0] inside m[1] rows of the best model, inside m[2] of the next, … (`multi._nested`)."""
    total, prev = 1 / m[0], m[0]
    for r, mi in zip(rho, m[1:]):
        total -= (1 / prev - 1 / min(mi, N)) * r ** 2
        prev = min(mi, N)
    return share * (1 - share) * total


def across(regions, w2, N):
    """The cheapest single design drawn across the table (labels, and the better model on a sample, at random from
    every row; the active version on every row) that brings every region to ± TARGET: each region gets its share of
    the labels and the sample. Searched over the sample-to-labels ratio (0: the better model left out)."""
    V = (TARGET / Z) ** 2
    best = None
    for q in [0.0] + list(np.geomspace(1, 1e5, 241)):
        def ok(m0):
            for r in regions:
                n0, n1, Ng = m0 * r["frac"], q * m0 * r["frac"], N * r["frac"]
                if n0 * min(r["share"], 1 - r["share"]) < MIN_EACH_SIDE:
                    return False
                two = q > 0 and r["rho2"] > r["rho1"]
                rho, m = ([r["rho2"], r["rho1"]], [n0, n1, Ng]) if two else ([r["rho1"]], [n0, Ng])
                if _var(r["share"], rho, m, Ng) > V:
                    return False
            return True
        lo, hi = 1.0, float(N)
        if not ok(hi):
            continue
        for _ in range(60):
            mid = (lo + hi) / 2
            lo, hi = (mid, hi) if not ok(mid) else (lo, mid)
        cost = LABEL * hi + w2 * min(N, q * hi)
        if best is None or cost < best["cost"]:
            best = {"labels": math.ceil(hi), "rows": math.ceil(min(N, q * hi)), "cost": cost}
    return best


def costs(regions, whole, w2):
    """± TARGET in every region, and on the whole table, per table size."""
    out = {}
    for N in TABLES:
        per = {}
        for r in regions:
            Ng = round(N * r["frac"])
            ps = M.plan(r["share"], [r["rho2"], r["rho1"]], [w2, 0.0], LABEL, Ng, TARGET, Z, ["v2", "v1"],
                        judged=[0, Ng])
            per[r["g"]] = {"cheapest": ps[0], "active": next(p for p in ps if p["models"] == ["v1"]),
                           "labels alone": next(p for p in ps if p["kind"] == "labels alone")}
        tot = {k: {"cost": sum(p[k]["cost"] for p in per.values()), "labels": sum(p[k]["labels"] for p in per.values()),
                   "rows": sum(p[k]["rows"].get("v2", 0) for p in per.values())} for k in ("cheapest", "active", "labels alone")}
        w = M.plan(whole["share"], [whole["rho2"], whole["rho1"]], [w2, 0.0], LABEL, N, TARGET, Z, ["v2", "v1"],
                   judged=[0, N])[0]
        out[N] = {"regions": per, "totals": tot, "across": across(regions, w2, N),
                  "whole": {"cost": w["cost"], "labels": w["labels"], "rows": w["rows"].get("v2", 0)}}
    return out


def _stats(y, f1, f2, draws, n0, m1, Ng, p1, two=True):
    """The pattern sums for labels on the first n0 drawn rows, the better model on the first m1 and the active version
    on every row: the drawn rows as drawn, the rest of the region's Ng rows at the active version's mean p1."""
    P = max(n0, m1)
    idx = draws[:P]
    cols = [np.where(np.arange(P) < n0, y[idx], NAN), f1[idx]]
    if two:
        cols.append(np.where(np.arange(P) < m1, f2[idx], NAN))
    st = M.pattern_stats(["labels", "v1", "v2"][:len(cols)], cols)
    rest = Ng - P
    if rest > 0:
        p = next((q for q in st["patterns"] if q["mask"] == (1,)), None)
        if p is None:
            st["patterns"].append({"mask": (1,), "n": rest, "sum": [rest * p1], "sq": {1: rest * p1}, "prod": {}})
        else:
            p["n"] += rest
            p["sum"][0] += rest * p1
            p["sq"][1] += rest * p1
    return st


def rounds(y, f1, f2, region, w2, runs, rng, two=True, N=TABLES[-1]):
    """`estimate --with 2 --by region --target --queue --draw` in rounds on a table of N rows (two=False: `estimate --by`
    with the active version alone): each round plans every region from what it has and carries the plan out, until
    every region's plan asks for nothing more."""
    regions = {g: np.where(region == g)[0] for g in REGIONS}
    frac = {g: len(i) / len(y) for g, i in regions.items()}
    truth = {g: float(y[i].mean()) for g, i in regions.items()}
    p1 = {g: float(f1[i].mean()) for g, i in regions.items()}
    vs = _versions("active", w2)[:2 if two else 1]
    out = []
    for _ in range(runs):
        draws = {g: rng.choice(i, 400_000) for g, i in regions.items()}
        n0, m1 = dict.fromkeys(REGIONS, 0), dict.fromkeys(REGIONS, 0)
        for done in range(ROUNDS + 1):                       # plan, carry it out; the last pass only estimates
            per = []
            for g in REGIONS:
                Ng = round(N * frac[g])
                st = _stats(y, f1, f2, draws[g], n0[g], m1[g], Ng, p1[g], two)
                per.append((g, M.together(st, Ng, [dict(v) for v in vs], Z, TARGET, LABEL)))
            plans = {g: r["plan"][0] for g, r in per}
            if done == ROUNDS or not any(p["more_labels"] or any(p["more_rows"].values()) for p in plans.values()):
                break
            for g, p in plans.items():
                n0[g] = max(n0[g], p["labels"])
                m1[g] = max(m1[g], p["rows"].get("v2", 0))
            assert max(max(n0.values()), max(m1.values())) <= 400_000
        final = {g: r["estimate"] for g, r in per}
        out.append({"rounds": done, "labels": sum(n0.values()), "rows": sum(m1.values()),
                    "cost": LABEL * sum(n0.values()) + w2 * sum(m1.values()),
                    "held": {g: bool(e["theta"] is not None and abs(e["theta"] - truth[g]) <= Z * e["se"])
                             for g, e in final.items()},
                    "half_width": {g: Z * e["se"] if e["se"] is not None else None for g, e in final.items()}})
    held = [h for o in out for h in o["held"].values()]
    widths = [w for o in out for w in o["half_width"].values() if w is not None]
    return {"runs": runs, "rounds": float(np.mean([o["rounds"] for o in out])),
            "labels": float(np.mean([o["labels"] for o in out])), "labels_p90": float(np.percentile([o["labels"] for o in out], 90)),
            "rows": float(np.mean([o["rows"] for o in out])), "cost": float(np.mean([o["cost"] for o in out])),
            "coverage": float(np.mean(held)), "half_width": float(np.mean(widths)),
            "within": float(np.mean(np.array(widths) <= TARGET * 1.05)),
            "coverage_by_region": {g: float(np.mean([o["held"][g] for o in out])) for g in REGIONS}}


def run(trials=300, runs=100, seed=0):
    data = load()
    rng = np.random.default_rng(seed)
    region = data["region"]
    w2 = data["cost"][BETTER]
    res = {"rows": {g: int((region == g).sum()) for g in REGIONS}, "cost": data["cost"], "trials": trials,
           "runs": runs, "target": TARGET, "setups": {}}
    for key, active in SETUPS.items():
        S = res["setups"][key] = {"active": active, "answers": {}}
        for ans in ANSWERS:
            y = (data["truth"] == ans).astype(float)
            f1 = (data["models"][active] == ans).astype(float)
            f2 = (data["models"][BETTER] == ans).astype(float)
            regions = []
            for g in REGIONS:
                m = region == g
                regions.append({"g": g, "frac": float(m.mean()), "share": float(y[m].mean()),
                                "rho1": float(np.corrcoef(y[m], f1[m])[0, 1]), "rho2": float(np.corrcoef(y[m], f2[m])[0, 1]),
                                "bias1": float(f1[m].mean() - y[m].mean()), "bias2": float(f2[m].mean() - y[m].mean())})
            whole = {"share": float(y.mean()), "rho1": float(np.corrcoef(y, f1)[0, 1]),
                     "rho2": float(np.corrcoef(y, f2)[0, 1])}
            S["answers"][ans] = {
                "regions": regions, "whole": whole,
                "coverage": coverage(y, f1, f2, region, trials, rng),
                "costs": costs(regions, whole, w2),
                "rounds": {"with": rounds(y, f1, f2, region, w2, runs, rng),
                           "active": rounds(y, f1, f2, region, w2, runs, rng, two=False)}}
            print(key, ans, "done", flush=True)
    os.makedirs(OUT, exist_ok=True)
    with open(f"{OUT}/results.json", "w") as fh:
        json.dump(res, fh, indent=2, default=float)
    with open(f"{OUT}/report.md", "w") as fh:
        fh.write(render(res))
    return OUT


def render(res):
    pct = lambda x: f"{100 * x:.1f} %"
    usd = lambda x: f"${x:,.0f}"
    L = ["# Several models and labels, region by region, on real answers", "",
         f"No API calls. Generated by `jevbench multigroupvalidate`. {sum(res['rows'].values()):,} CFPB complaints from "
         "the four US Census regions (" + ", ".join(f"{g} {n:,}" for g, n in res["rows"].items()) + "), each with the "
         "product the consumer chose and three stored sets of answers. The share of complaints about each product is "
         f"wanted in every region to ± {100 * res['target']:.0f} points (95 %). The active version answers every row "
         f"(setup A: Jev at 20 rows per request; B: Laya), and `--with` adds {BETTER} at "
         f"${res['cost'][BETTER] * 1e6:.2f} per million rows on a sample in each region. Labels cost $1.", "",
         f"Coverage: {res['trials']} draws per cell on {COPIES}× each region's complaints, resampled; counted where the "
         f"region's labels hold {MIN_EACH_SIDE} with and without the answer. Rounds: {res['runs']} runs per cell on a "
         f"table of {TABLES[-1]:,} rows.", ""]
    for key, S in res["setups"].items():
        L += [f"## Setup {key}: the active version is {S['active']}", ""]
        for ans, a in S["answers"].items():
            L += [f"### {ans.capitalize()}", "",
                  "| region | true share | active version's error | better model's error | correlation with the labels: active, better |",
                  "| --- | ---: | ---: | ---: | ---: |"]
            for r in a["regions"]:
                L.append(f"| {r['g']} | {pct(r['share'])} | {100 * r['bias1']:+.1f} | {100 * r['bias2']:+.1f} | "
                         f"{r['rho1']:.2f}, {r['rho2']:.2f} |")
            L += ["", "**1. Coverage** (intervals holding the region's share; mean half-width in points).", "",
                  "| design | estimate | " + " | ".join(REGIONS) + " | all regions |",
                  "| --- | --- | " + " | ".join("---:" for _ in REGIONS) + " | ---: |"]
            names = {"with": "labels, active, better (`--with --by`)", "active": "labels, active (`--by`)",
                     "labels": "labels alone"}
            for d, c in a["coverage"].items():
                for k, tag in names.items():
                    cells = []
                    for g in REGIONS + ("all regions",):
                        x = c[k].get(g)
                        cells.append("–" if not x else f"{pct(x['coverage'])} ±{100 * x['half_width']:.1f}"
                                     + ("" if x["draws"] >= 0.9 * x["of"] else f" ({x['draws']} draws)"))
                    L.append(f"| {d} | {tag} | " + " | ".join(cells) + " |")
            L += ["", f"**2. Cost of ± {100 * res['target']:.0f} points in every region** (labels; rows the better "
                      "model judges; cost).", "",
                  "| rows | each region's cheapest mix | active version alone in each region | labels alone | "
                  "one design across the table | ± 3 on the whole table |", "| ---: | ---: | ---: | ---: | ---: | ---: |"]
            for N, c in a["costs"].items():
                t = c["totals"]
                cell = lambda x: f"{usd(x['cost'])} ({x['labels']:,}" + (f"; {x['rows']:,}" if x.get("rows") else "") + ")"
                L.append(f"| {int(N):,} | {cell(t['cheapest'])} | {cell(t['active'])} | {cell(t['labels alone'])} | "
                         f"{cell(c['across'])} | {cell(c['whole'])} |")
            L += ["", f"**3. In rounds on {TABLES[-1]:,} rows** (a pilot, then each region's cheapest mix).", "",
                  "| | rounds | labels (p90) | rows judged by the better model | cost | intervals held | half-width | within 5 % of target |",
                  "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
            for k, tag in (("with", "`--with 2 --by`"), ("active", "`--by`, active version alone")):
                rr = a["rounds"][k]
                L.append(f"| {tag} | {rr['rounds']:.1f} | {rr['labels']:,.0f} ({rr['labels_p90']:,.0f}) | "
                         f"{rr['rows']:,.0f} | {usd(rr['cost'])} | {pct(rr['coverage'])} | {100 * rr['half_width']:.2f} | "
                         f"{pct(rr['within'])} |")
            L.append("")
    return "\n".join(L) + "\n"
