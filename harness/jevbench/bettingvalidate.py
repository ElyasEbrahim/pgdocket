"""Betting intervals below the floor, on real answers (#30, no API calls).

The normal interval needs 10 labelled rows with and without the answer before it can be trusted, so for a rare answer
the floor sets the number of labels. Below it, pgdocket now gives a betting interval (`pgdocket.betting`), valid at any
number of labels. Two questions, on stored answers whose true labels are known:

1. **Does it hold?** The whole scope as the population, n labels drawn at random, many times: how often each interval
   holds the true share. The normal interval in every draw, and in the draws above and below the floor; the betting
   interval; and what pgdocket now reports, the normal interval above the floor and betting below it.
   - Kubernetes issues: 500, which SIG, answered by Jev 1.13 and by Laya (`pgdocket check` runs);
   - CFPB complaints: 2,000, which product, answered by Jev 1.13 one row per request and by Laya.
2. **Does it save labels?** Shares by group (`estimate --by --target --draw`), round after round until every group is
   within ± target, as #25 measured them: the plan with the floor only, against the plan that also weighs betting.
   CFPB complaints by US region and by year, four answers from 12 % of complaints down to 1.8 %.
"""
import json
import math
import os

import numpy as np

from pgdocket import betting
from pgdocket import groups as G
from pgdocket.estimate import MIN_EACH_SIDE, norm_ppf, ppi_share

from . import groupvalidate as GV
from .ppivalidate import load as load_check

OUT = "results/betting-validation"
SIZES = (50, 100, 200, 300)
GROUP_ANSWERS = ("debt collection", "credit card", "mortgage", "student loan")
TARGETS = (0.05, 0.03)
MODELS = {"Jev": "Jev, 1 row per request", "Laya": "Laya, 1 row per request"}
ROUNDS = 10
Z = norm_ppf(0.975)


def datasets():
    """Per dataset: the answers, and per model the 0/1 arrays (y, f) for each answer."""
    sig = json.load(open("examples/k8s_sig_options.json"))
    out = {}
    k8s = {}
    for model, path in (("Jev", "results/check-k8s-sig/judgments.jsonl"),
                        ("Laya", "results/check-laya-k8s-sig/judgments.jsonl")):
        names, answers, labels = load_check(path, "choice", options=sig)
        k8s[model] = {a: (np.array([l == a for l in labels], float), np.array([x == a for x in answers], float))
                      for a in names}
    out["Kubernetes issues (500), which SIG"] = k8s
    data = GV.load()
    cfpb = {}
    for model, run in MODELS.items():
        said = data["models"][run]
        cfpb[model] = {a: ((data["truth"] == a).astype(float), (said == a).astype(float))
                       for a in sorted(set(data["truth"]))}
    out["CFPB complaints (2,000), which product"] = cfpb
    return out, data


def coverage(y, f, n, draws, rng):
    """n labels drawn at random from all rows, `draws` times: each interval's hits and half-widths."""
    N, truth = len(y), float(y.mean())
    rec = {k: [] for k in ("normal", "normal_above", "normal_below", "betting", "reported")}
    below = 0
    for _ in range(draws):
        idx = rng.permutation(N)[:n]
        lab = np.zeros(N, bool)
        lab[idx] = True
        r = ppi_share(f[~lab], f[lab], y[lab])
        ci = betting.interval(y[idx], f[idx], f.sum(), N)
        bet = (False, None) if ci is None else (ci[0] <= truth <= ci[1], (ci[1] - ci[0]) / 2)   # empty: a miss
        rec["betting"].append(bet)
        k = int(y[idx].sum())
        above = min(k, n - k) >= MIN_EACH_SIDE
        below += not above
        if r["estimate"] is None:                        # no labelled row on one side: no normal interval
            rec["normal"].append((False, None))
            rec["normal_below"].append((False, None))
            rec["reported"].append(bet)
            continue
        norm = (r["ci_low"] <= truth <= r["ci_high"], (r["ci_high"] - r["ci_low"]) / 2)
        rec["normal"].append(norm)
        rec["normal_above" if above else "normal_below"].append(norm)
        rec["reported"].append(norm if above else bet)
    summary = lambda v: None if not v else {
        "coverage": float(np.mean([h for h, _ in v])), "draws": len(v),
        "half_width": float(np.mean([w for _, w in v if w is not None])) if any(w is not None for _, w in v) else None}
    return {"truth": truth, "below_floor": below / draws, **{k: summary(v) for k, v in rec.items()}}


def _estimates(col, y, f, order, use_betting):
    """`estimate --by`: each group's estimate from the labels drawn so far (`order`: per group, its labelled rows in
    the order drawn), with betting below the floor when `use_betting`."""
    gs = []
    for g in sorted(order):
        rows = np.flatnonzero(col == g)
        lab = np.zeros(len(rows), bool)
        lab[np.searchsorted(rows, order[g])] = True
        gs.append(G.sums(g, np.ones(len(rows)), f[rows], y[rows], lab, lab))
    gs = G.summarise(gs, Z)
    if use_betting:
        for g in gs:
            if g["judged"] and not g["reliable"] and not (g["est"] and g["est"]["exact"]) and len(order[g["g"]]):
                G.bet(g, y[order[g["g"]]], f[order[g["g"]]], Z)
    return gs


def _met(gs, target, only):
    return all(g["est"] is not None and g["reliable"] and (g["est"]["exact"] or Z * g["est"]["se"] <= target)
               for g in gs if g["judged"] and (only is None or g["g"] in only))


def rounds(col, y, f, target, use_betting, rng, only=None):
    """What `estimate --by --target --draw` does, round after round: a pilot, then each group's own requirement, with
    the plan that weighs betting or the floor only. Labels drawn at random within each group, in the order drawn."""
    order = {g: np.array([], int) for g in sorted(set(col))}
    for _ in range(ROUNDS):
        gs = _estimates(col, y, f, order, use_betting)
        p = G.plan([g for g in gs if only is None or g["g"] in only], target, Z, bet=use_betting)
        if p["more"] == 0:
            break
        for r in p["groups"]:
            pool = np.setdiff1d(np.flatnonzero(col == r["group"]), order[r["group"]])
            order[r["group"]] = np.r_[order[r["group"]], rng.permutation(pool)[:r["more"]]]
    gs = _estimates(col, y, f, order, use_betting)
    return sum(len(v) for v in order.values()), gs


def allocation(col, y, f, target, runs, rng, only=None):
    truth = {g: float(y[col == g].mean()) for g in set(col)}
    out = {}
    for name, use in (("floor only", False), ("with betting", True)):
        used, held, every, done, by_betting = [], 0, 0, 0, 0
        for _ in range(runs):
            n, gs = rounds(col, y, f, target, use, rng, only)
            used.append(n)
            for g in gs:
                if g["est"] and (only is None or g["g"] in only):
                    held += g["ci"][0] <= truth[g["g"]] <= g["ci"][1] if not g["est"]["exact"] else True
                    every += 1
                    by_betting += bool(g.get("interval"))
            done += _met(gs, target, only)
        out[name] = {"labels": float(np.mean(used)), "labels_p90": float(np.percentile(used, 90)),
                     "group_coverage": held / every, "reached": done / runs, "betting_share": by_betting / every}
    return out


def run(draws=500, runs=100, seed=0):
    rng = np.random.default_rng(seed)
    sets, data = datasets()
    res = {"draws": draws, "runs": runs, "coverage": {}, "groups": {}}
    for name, models in sets.items():
        res["coverage"][name] = {}
        for model, answers in models.items():
            res["coverage"][name][model] = {a: {n: coverage(y, f, n, draws, rng) for n in SIZES}
                                            for a, (y, f) in answers.items()}
            print(name, model, "coverage done", flush=True)
    for by in ("region", "year"):
        col = data[by]
        sizes = {g: int((col == g).sum()) for g in sorted(set(col))}
        big = sorted(g for g, n in sizes.items() if n >= 100)
        res["groups"][by] = {"sizes": sizes, "big": big, "answers": {}}
        for ans in GROUP_ANSWERS:
            y = (data["truth"] == ans).astype(float)
            entry = {"truth": {g: float(y[col == g].mean()) for g in sizes}, "models": {}}
            for model, run_id in MODELS.items():
                f = (data["models"][run_id] == ans).astype(float)
                entry["models"][model] = {
                    t: {"every group": allocation(col, y, f, t, runs, rng),
                        "groups of 100+ rows": allocation(col, y, f, t, runs, rng, set(big))} for t in TARGETS}
                print(by, ans, model, "rounds done", flush=True)
            res["groups"][by]["answers"][ans] = entry
    os.makedirs(OUT, exist_ok=True)
    with open(f"{OUT}/results.json", "w") as fh:
        json.dump(res, fh, indent=2, default=float)
    with open(f"{OUT}/report.md", "w") as fh:
        fh.write(render(res))
    return OUT


def render(res):
    pct = lambda x: "–" if x is None else f"{100 * x:.1f} %"
    cell = lambda c: "–" if not c else (f"{pct(c['coverage'])}" + (f" ±{100 * c['half_width']:.1f}" if c["half_width"] else "")
                                       + (f" ({c['draws']})" if c["draws"] < res["draws"] else ""))
    L = ["# Betting intervals below the floor, on real answers", "",
         f"No API calls. Generated by `jevbench bettingvalidate`. Coverage: {res['draws']} draws of n labels at random "
         "from all rows, per answer and n; half-widths in points. Below the floor: draws whose labels hold fewer than "
         f"{MIN_EACH_SIDE} rows with or without the answer. Reported: what pgdocket now gives, the normal interval above "
         "the floor and betting below it. Normal intervals are missing (counted as misses) when the labels hold no row "
         f"on one side. Rounds: {res['runs']} runs per cell.", ""]
    for name, models in res["coverage"].items():
        L += [f"## {name}: does it hold?", ""]
        for model, answers in models.items():
            L += [f"### {model}", "", "| answer | share | n | below the floor | normal | normal, above | normal, below | "
                  "betting | reported |", "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
            for a, by_n in sorted(answers.items(), key=lambda kv: kv[1][SIZES[0]]["truth"]):
                for n, c in by_n.items():
                    L.append(f"| {a} | {pct(c['truth'])} | {n} | {pct(c['below_floor'])} | {cell(c['normal'])} | "
                             f"{cell(c['normal_above'])} | {cell(c['normal_below'])} | {cell(c['betting'])} | "
                             f"{cell(c['reported'])} |")
            L.append("")
    for by, g in res["groups"].items():
        L += [f"## By {by}: does it save labels?", "", "Rows: " + ", ".join(f"{k} {v:,}" for k, v in g["sizes"].items())
              + ".", "", "| answer | model | target | groups | floor only: labels (p90) | held | with betting: labels "
              "(p90) | held | betting intervals | saved |", "| --- | --- | ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: |"]
        for a, e in g["answers"].items():
            for model, by_t in e["models"].items():
                for t, sets in by_t.items():
                    for which, r in sets.items():
                        fo, wb = r["floor only"], r["with betting"]
                        L.append(f"| {a} | {model} | ± {100 * float(t):.0f} | {which} | {fo['labels']:,.0f} "
                                 f"({fo['labels_p90']:,.0f}) | {pct(fo['group_coverage'])} | {wb['labels']:,.0f} "
                                 f"({wb['labels_p90']:,.0f}) | {pct(wb['group_coverage'])} | {pct(wb['betting_share'])} | "
                                 f"{pct(1 - wb['labels'] / fo['labels'])} |")
        L.append("")
    return "\n".join(L) + "\n"
