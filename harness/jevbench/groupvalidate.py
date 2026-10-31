"""Shares by group, on real answers (#25, no API calls): how row-level mistakes reach a GROUP BY result, and what it
costs in human labels to make every group's figure reliable.

The 2,000 CFPB complaints carry the product the consumer chose (the labels), the state they wrote from (from the
CC0 source shard, mapped to US Census regions) and the year. Three stored sets of answers to "which product?" judge
them: Jev 1.13 at 20 rows per request (Phase 0), Jev 1.13 one row per request, and Laya one row per request. The query
is "share of complaints about X, by region" (and by year). Every label is known, so each method can be checked:

1. systematic error: each model's share minus the true share, group by group;
2. coverage with labels drawn at random across the table: each group corrected with its own labels (`estimate --by`)
   against one correction fitted on all labels;
3. labels needed to bring every group to ± target: random labels across the table, added until every group is there,
   against `estimate --by --target --draw` rounds (a pilot, then each group's own requirement);
4. the same requirement with each model, and with labels alone.
"""
import json
import math
import os

import numpy as np

from pgdocket import groups as G
from pgdocket.estimate import norm_ppf

from .db import connect

OUT = "results/group-validation"
SHARD = "data/raw/hf/train-00000-of-00003.parquet"
RUNS = {"Jev, 20 rows per request": "jev-20261017-122314", "Jev, 1 row per request": "jev-20261019-073856",
        "Laya, 1 row per request": "laya-20261030-103603"}
REGION = {**{s: "Northeast" for s in "CT ME MA NH RI VT NJ NY PA".split()},
          **{s: "Midwest" for s in "IL IN MI OH WI IA KS MN MO NE ND SD".split()},
          **{s: "South" for s in "DE DC FL GA MD NC SC VA WV AL KY MS TN AR LA OK TX".split()},
          **{s: "West" for s in "AZ CO ID MT NV NM UT WY AK CA HI OR WA".split()}}
ANSWERS = ("debt collection", "credit card")
Z = norm_ppf(0.975)


def load():
    """Per complaint: truth, region, year and each model's answer (choice_base), in one order."""
    import pyarrow.parquet as pq
    t = pq.read_table(SHARD, columns=["Complaint ID", "State"])
    state = {str(i): s for i, s in zip(t.column("Complaint ID").to_pylist(), t.column("State").to_pylist())}
    with connect() as conn:
        rows = conn.execute("SELECT complaint_id, text_hash, product, extract(year FROM date_received)::int "
                            "FROM bench.complaints ORDER BY complaint_id").fetchall()
        ans = {}
        for name, run in RUNS.items():
            ans[name] = {h: a["choice"] for h, a in conn.execute(
                "SELECT text_hash, answer FROM bench.results WHERE run_id = %s AND experiment = 'choice_base'", (run,))}
    keep = [r for r in rows if all(r[1] in a for a in ans.values())]
    return {"truth": np.array([r[2] for r in keep]),
            "region": np.array([REGION.get(state.get(str(r[0])), "other or unknown") for r in keep]),
            "year": np.array([str(r[3]) for r in keep]),
            "models": {name: np.array([a[r[1]] for r in keep]) for name, a in ans.items()}}


def stats(groupcol, f, y, lab):
    """`group_stats` rows for in-memory data: per group, the counts `ppi_binary` needs."""
    out = []
    for g in sorted(set(groupcol)):
        m = groupcol == g
        L, U = m & lab, m & ~lab
        out.append({"g": g, "judged": int(m.sum()), "n": int(L.sum()), "k": int(y[L].sum()), "sf_l": int(f[L].sum()),
                    "syf": int((f * y)[L].sum()), "sf_u": int(f[U].sum())})
    return out


def pooled(groupcol, f, y, lab):
    """One correction for all groups: each group's model share plus the mean label-minus-model over all labels."""
    d = (y - f)[lab]
    corr, var_d = d.mean(), d.var(ddof=1)
    out = {}
    for g in sorted(set(groupcol)):
        m = groupcol == g
        fg = f[m & ~lab]
        theta = f[m].mean() + corr
        se = math.sqrt(var_d / lab.sum() + (fg.var(ddof=1) / len(fg) if len(fg) > 1 else 0.0))
        out[g] = (theta - Z * se, theta + Z * se)
    return out


def coverage(groupcol, f, y, n_labels, trials, rng):
    """Labels drawn at random across the table: per group, how often each method's interval holds the truth."""
    truth = {g: y[groupcol == g].mean() for g in set(groupcol)}
    hit = {g: {"own": 0, "own_n": 0, "pooled": 0, "width": [], "labels_only_width": []} for g in truth}
    for _ in range(trials):
        lab = np.zeros(len(y), bool)
        lab[rng.choice(len(y), n_labels, replace=False)] = True
        for g in G.summarise(stats(groupcol, f, y, lab), Z):
            if g["reliable"] and not g["est"]["exact"]:
                h = hit[g["g"]]
                h["own_n"] += 1
                h["own"] += g["ci"][0] <= truth[g["g"]] <= g["ci"][1]
                h["width"].append(Z * g["est"]["se"])
                yl = y[(groupcol == g["g"]) & lab]
                h["labels_only_width"].append(Z * yl.std(ddof=1) / math.sqrt(len(yl)))
        for g, (lo, hi) in pooled(groupcol, f, y, lab).items():
            hit[g]["pooled"] += lo <= truth[g] <= hi
    return {g: {"truth": float(truth[g]), "rows": int((groupcol == g).sum()),
                "own": h["own"] / h["own_n"] if h["own_n"] else None, "own_draws": h["own_n"],
                "pooled": h["pooled"] / trials,
                "half_width": float(np.mean(h["width"])) if h["width"] else None,
                "labels_only_half_width": float(np.mean(h["labels_only_width"])) if h["labels_only_width"] else None}
            for g, h in sorted(hit.items())}


def met(gs, target, only=None):
    return all(g["est"] is not None and g["reliable"] and (g["est"]["exact"] or Z * g["est"]["se"] <= target)
               for g in gs if g["judged"] and (only is None or g["g"] in only))


def uniform(groupcol, f, y, target, rng, only=None, step=25):
    """Labels drawn at random across the table, `step` at a time, until every group is reliable and within target."""
    order = rng.permutation(len(y))
    for n in range(step, len(y) + step, step):
        lab = np.zeros(len(y), bool)
        lab[order[:n]] = True
        gs = G.summarise(stats(groupcol, f, y, lab), Z)
        if met(gs, target, only):
            return n, gs
    return len(y), gs


def stratified(groupcol, f, y, target, rng, only=None, rounds=8):
    """What `estimate --by --target --draw` does, round after round: a pilot, then each group's own requirement."""
    lab = np.zeros(len(y), bool)
    for _ in range(rounds):
        gs = G.summarise(stats(groupcol, f, y, lab), Z)
        p = G.plan([g for g in gs if only is None or g["g"] in only], target, Z)
        if p["more"] == 0:
            break
        for r in p["groups"]:
            pool = np.flatnonzero((groupcol == r["group"]) & ~lab)
            lab[rng.choice(pool, min(r["more"], len(pool)), replace=False)] = True
    gs = G.summarise(stats(groupcol, f, y, lab), Z)
    return int(lab.sum()), gs


def allocation(groupcol, f, y, target, trials, rng, only=None):
    """Labels used, and how often every targeted group's final interval held its truth, for both ways of drawing
    labels. `only`: the groups that must reach the target (default: all)."""
    truth = {g: y[groupcol == g].mean() for g in set(groupcol)}
    out = {}
    for name, fn in (("across the table", uniform), ("within groups", stratified)):
        used, held, every, done = [], 0, 0, 0
        for _ in range(trials):
            n, gs = fn(groupcol, f, y, target, rng, only)
            used.append(n)
            ok = [g["ci"][0] <= truth[g["g"]] <= g["ci"][1] for g in gs if g["est"] and (only is None or g["g"] in only)]
            held += sum(ok)
            every += len(ok)
            done += met(gs, target, only)
        out[name] = {"labels": float(np.mean(used)), "labels_p90": float(np.percentile(used, 90)),
                     "group_coverage": held / every, "reached": done / trials}
    return out


def run(trials=500, seed=0):
    data = load()
    rng = np.random.default_rng(seed)
    res = {"rows": int(len(data["truth"])), "groupings": {}, "trials": trials}
    for by in ("region", "year"):
        col = data[by]
        sizes = {g: int((col == g).sum()) for g in sorted(set(col))}
        big = {g for g, n in sizes.items() if n >= 100}
        res["groupings"][by] = {"sizes": sizes, "answers": {}, "big": sorted(big)}
        for ans in ANSWERS:
            y = (data["truth"] == ans).astype(int)
            entry = {"truth": {g: float(y[col == g].mean()) for g in sizes}, "models": {}}
            for model, said in data["models"].items():
                f = (said == ans).astype(int)
                m = {"bias": {g: float(f[col == g].mean() - y[col == g].mean()) for g in sizes},
                     "disagree": {g: float((f[col == g] != y[col == g]).mean()) for g in sizes}}
                lab = np.zeros(len(y), bool)
                lab[rng.choice(len(y), 400, replace=False)] = True
                m["flagged_400"] = [x["group"] for x in G.systematic(G.summarise(stats(col, f, y, lab), Z))]
                m["coverage"] = {n: coverage(col, f, y, n, trials, rng) for n in (400, 800)}
                m["allocation"] = {"every group": allocation(col, f, y, 0.05, max(100, trials // 5), rng),
                                   "groups of 100+ rows": allocation(col, f, y, 0.05, max(100, trials // 5), rng, big)}
                entry["models"][model] = m
            zero = np.zeros(len(y), int)                       # labels alone: a "model" that never says yes
            entry["labels_only_allocation"] = {
                "every group": allocation(col, zero, y, 0.05, max(100, trials // 5), rng),
                "groups of 100+ rows": allocation(col, zero, y, 0.05, max(100, trials // 5), rng, big)}
            res["groupings"][by]["answers"][ans] = entry
    os.makedirs(OUT, exist_ok=True)
    with open(f"{OUT}/results.json", "w") as f:
        json.dump(res, f, indent=2, default=float)
    with open(f"{OUT}/report.md", "w") as f:
        f.write(render(res))
    return OUT


def render(res):
    pct = lambda x: "–" if x is None else f"{100 * x:.1f} %"
    pts = lambda x: f"{100 * x:+.1f}"
    L = ["# Shares by group, on real answers", "",
         f"No API calls. Generated by `jevbench groupvalidate`: {res['rows']:,} CFPB complaints, each with the product the "
         "consumer chose, the region (US Census, from the state) and the year, judged by three stored sets of answers. "
         f"Coverage: {res['trials']:,} random draws of labels; allocation: {max(100, res['trials'] // 5)} runs. "
         "Intervals at 95 %.", ""]
    for by, gdata in res["groupings"].items():
        sizes = gdata["sizes"]
        L += [f"## By {by}", "", "Rows: " + ", ".join(f"{g} {n:,}" for g, n in sizes.items()) + ".", ""]
        for ans, e in gdata["answers"].items():
            models = list(e["models"])
            L += [f"### Share of complaints about {ans}", "",
                  "**1. Systematic error.** The model's share minus the true share, in points; flagged: groups "
                  "`estimate --by` flags with 400 labels drawn at random.", "",
                  "| " + by + " | true share | " + " | ".join(models) + " |", "| --- | ---: | " + " | ".join("---:" for _ in models) + " |"]
            for g in sizes:
                L.append(f"| {g} | {pct(e['truth'][g])} | " + " | ".join(
                    pts(e["models"][mo]["bias"][g]) + (" ⚑" if g in e["models"][mo]["flagged_400"] else "")
                    for mo in models) + " |")
            for n in next(iter(e["models"].values()))["coverage"]:
                L += ["", f"**2. Coverage, {n} labels drawn at random across the table.** Each group corrected with its "
                      "own labels (when it has enough) against one correction fitted on all labels.", "",
                      "| model | " + " | ".join(sizes) + " |", "| --- | " + " | ".join("---:" for _ in sizes) + " |"]
                for mo in models:
                    c = e["models"][mo]["coverage"][n]
                    L.append(f"| {mo}, own labels | " + " | ".join(
                        (f"{pct(c[g]['own'])} ±{100 * c[g]['half_width']:.1f}"
                         + (f" (in {c[g]['own_draws']} draws)" if c[g]["own_draws"] < 0.9 * res["trials"] else ""))
                        if c[g]["own"] is not None else "–" for g in sizes) + " |")
                    L.append(f"| {mo}, one correction | " + " | ".join(pct(c[g]["pooled"]) for g in sizes) + " |")
            for t in next(iter(e["models"].values()))["allocation"]:
                scope = f"every {by}" if t == "every group" else f"every {by} with 100 or more rows ({', '.join(gdata['big'])})"
                L += ["", f"**3–4. Labels to bring {scope} to ± 5 points** (mean, and 90th percentile; share of the "
                      "targeted groups' final intervals that held the truth).", "",
                      "| predictor | across the table | within groups (`--target --draw`) |", "| --- | ---: | ---: |"]
                rows = [(mo, e["models"][mo]["allocation"][t]) for mo in models] + [("labels alone", e["labels_only_allocation"][t])]
                for mo, a in rows:
                    u, s = a["across the table"], a["within groups"]
                    L.append(f"| {mo} | {u['labels']:,.0f} (p90 {u['labels_p90']:,.0f}; {pct(u['group_coverage'])}) | "
                             f"{s['labels']:,.0f} (p90 {s['labels_p90']:,.0f}; {pct(s['group_coverage'])}) |")
            L.append("")
    return "\n".join(L) + "\n"
