"""Weighted shares and join fan-out, on real answers (#26, no API calls): when a query counts a row many times, how
the row's mistakes reach the result, and which rows need a person.

The 2,000 CFPB narratives judged in Phase 0 are distinct texts. In the source shard (563,191 complaints) many
complaints carry a narrative word for word: dispute letters sent to each credit bureau, templates, copies. Judging
each distinct text once and counting every complaint that carries it is a join with fan-out: the judged texts stand for
far more complaints than there are texts, some for thousands. The query is "share of these complaints about X", over
all of them and by the region each complaint came from (a text's copies come from many regions, so each region is its
own weighted query); each text's label (the product its sampled complaint chose) and answer are taken to hold for
every copy.

1. coverage with labels drawn at random among the texts: the weighted estimate (`estimate --weight`) against two
   shortcuts, the texts' own share (weights ignored) and copies counted as labelled rows (one judgment and one label
   treated as many independent ones); and with labels drawn by weight (`--target --draw`), at fixed sizes;
2. labels needed to reach ± target: texts drawn at random, the plan (drawn by weight, round after round) and
   complaints drawn at random (each draw labels its text);
3. the heaviest texts: how much of the result they hold and how often the models get them wrong.
"""
import collections
import hashlib
import json
import math
import os

import numpy as np

from pgdocket import groups as G
from pgdocket.estimate import MIN_EACH_SIDE, norm_ppf

from .db import connect
from .groupvalidate import REGION, RUNS, SHARD
from .tasks import PRODUCT_MAP

OUT = "results/weight-validation"
ANSWERS = ("credit reporting", "debt collection", "credit card")
REGIONS = ("Midwest", "Northeast", "South", "West")
TARGETS = {"all complaints": 0.02, **{g: 0.05 for g in REGIONS}}
Z = norm_ppf(0.975)


def load():
    """Per distinct text: truth, each model's answer, and its copies in the shard (all, and by the copy's region)."""
    import pyarrow.parquet as pq
    t = pq.read_table(SHARD, columns=["Consumer complaint narrative", "State", "Product"])
    copies, by_region, product = (collections.Counter(), collections.defaultdict(collections.Counter),
                                  collections.defaultdict(collections.Counter))
    for text, state, prod in zip(*(t.column(c).to_pylist() for c in t.column_names)):
        s = (text or "").strip()
        if len(s) < 40:
            continue
        h = hashlib.md5(s[:1500].encode("utf-8")).hexdigest()
        copies[h] += 1
        by_region[h][REGION.get(state, "other or unknown")] += 1
        product[h][PRODUCT_MAP.get(prod or "")] += 1
    with connect() as conn:
        rows = conn.execute("SELECT text_hash, product FROM bench.complaints ORDER BY complaint_id").fetchall()
        ans = {name: dict(conn.execute("SELECT text_hash, answer->>'choice' FROM bench.results "
                                       "WHERE run_id = %s AND experiment = 'choice_base'", (run,)).fetchall())
               for name, run in RUNS.items()}
    keep = [r for r in rows if all(r[0] in a for a in ans.values())]
    return {"truth": np.array([p for _, p in keep]), "keys": [h for h, _ in keep],
            "weights": {"all complaints": np.array([copies[h] for h, _ in keep], float),
                        **{g: np.array([by_region[h][g] for h, _ in keep], float) for g in REGIONS}},
            "same_label": sum(product[h][p] for h, p in keep) / sum(copies[h] for h, _ in keep),
            "models": {name: np.array([a[h] for h, _ in keep]) for name, a in ans.items()},
            "shard_rows": t.num_rows}


def estimate(w, f, y, labelled, uniform=None, u=None):
    """`estimate --weight` on one group of rows, from arrays."""
    uniform = labelled if uniform is None else uniform
    return G.summarise([G.sums(G.ALL, w, f, y, labelled, uniform, u)], Z)[0]


def top_rows(w, f, g, target):
    """What `heaviest_rows` returns for one group, from arrays."""
    thr = g["weight"] * target / (2 * Z * math.sqrt(g["judged"]))
    idx = np.flatnonzero((w > 0) & (w >= thr))
    idx = idx[np.lexsort((idx, -w[idx]))][:G.HEAVY_CAP]
    return {"thr": thr, "rows": [(float(w[i]), int(f[i])) for i in idx]}


def take(w, labelled, u, draw):
    """The rows a draw by weight asks to label (`groups.walk` down the ranking by u / w), from arrays."""
    idx = np.flatnonzero(w > 0)
    idx = idx[np.argsort(u[idx] / w[idx])]
    keys, _ = G.walk([(float(u[i] / w[i]), float(w[i]), bool(labelled[i]), int(i)) for i in idx], draw)
    return np.array(keys, int)


def copies_as_rows(w, f, y, lab):
    """Every copy a row of its own, a labelled text's copies all labelled: PPI++ over complaints that treats one
    judgment and one label as many independent ones."""
    L, U = lab & (w > 0), ~lab & (w > 0)
    return G.ppi_binary(int(w[L].sum()), int((w * y)[L].sum()), int((w * f)[L].sum()), int((w * f * y)[L].sum()),
                        int(w[U].sum()), int((w * f)[U].sum()))


def _held(est, truth):
    return est["theta"] - Z * est["se"] <= truth <= est["theta"] + Z * est["se"]


def coverage(w, f, y, trials, rng, random_n=(200, 400, 800), weight_n=(100, 150, 250)):
    """How often each interval holds the weighted truth: labels drawn at random among the texts (the weighted
    estimate, the texts' own share, copies as rows), and labels drawn by weight (the first n in the ranking)."""
    keep = w > 0
    w, f, y = w[keep], f[keep], y[keep]
    truth = float((w * y).sum() / w.sum())
    res = {"truth": truth, "texts": int(len(w)), "random": {}, "by weight": {}}
    for n in random_n:
        if n >= len(w):
            continue
        out = {k: [] for k in ("weighted", "reliable", "texts", "copies")}
        flagged = 0
        for _ in range(trials):
            lab = np.zeros(len(w), bool)
            lab[rng.choice(len(w), n, replace=False)] = True
            g = estimate(w, f, y, lab)
            if g["est"] and not g["est"]["exact"]:
                hit = g["ci"][0] <= truth <= g["ci"][1]
                out["weighted"].append((hit, Z * g["est"]["se"]))
                if g["reliable"]:
                    out["reliable"].append((hit, Z * g["est"]["se"]))
                flagged += min(g["effective"]) >= MIN_EACH_SIDE and not g["reliable"]
            t = G.ppi_binary(int(lab.sum()), int(y[lab].sum()), int(f[lab].sum()), int((f * y)[lab].sum()),
                             int((~lab).sum()), int(f[~lab].sum()))
            if t and not t["exact"]:
                out["texts"].append((_held(t, truth), Z * t["se"]))
            c = copies_as_rows(w, f, y, lab)
            if c and not c["exact"]:
                out["copies"].append((_held(c, truth), Z * c["se"]))
        res["random"][n] = {k: {"coverage": float(np.mean([h for h, _ in v])) if v else None,
                                "half_width": float(np.mean([x for _, x in v])) if v else None, "draws": len(v)}
                            for k, v in out.items()}
        res["random"][n]["flagged_heavy"] = flagged
    for n in weight_n:
        hits, rel, width = [], [], []
        for _ in range(trials):
            u = rng.random(len(w))
            lab = np.zeros(len(w), bool)
            lab[np.argsort(u / w)[:n]] = True
            g = estimate(w, f, y, lab, np.zeros(len(w), bool), u)
            if g["est"] is None or g["est"]["exact"] or g["method"] != "weight":
                continue
            hits.append(g["ci"][0] <= truth <= g["ci"][1])
            rel.append(g["reliable"])
            width.append(Z * g["est"]["se"])
        hits, rel = np.array(hits, bool), np.array(rel, bool)
        res["by weight"][n] = {"coverage": float(hits.mean()) if len(hits) else None,
                               "reliable_coverage": float(hits[rel].mean()) if rel.any() else None,
                               "reliable": float(rel.mean()) if len(rel) else None,
                               "half_width": float(np.mean(width)) if width else None, "draws": int(len(hits))}
    return res


def met(g, target):
    return g["est"] is not None and g["reliable"] and (g["est"]["exact"] or Z * g["est"]["se"] <= target)


def random_texts(w, f, y, target, rng, step=25):
    """Texts drawn at random, `step` at a time, until the weighted share is reliable and within target."""
    order = rng.permutation(len(w))
    for n in range(step, len(w) + step, step):
        lab = np.zeros(len(w), bool)
        lab[order[:n]] = True
        g = estimate(w, f, y, lab)
        if met(g, target):
            break
    return int(lab.sum()), g


def by_weight(w, f, y, target, rng, rounds=12):
    """What `estimate --weight --target --draw` and `label` do, round after round."""
    u = rng.random(len(w))
    lab, none = np.zeros(len(w), bool), np.zeros(len(w), bool)
    for _ in range(rounds):
        g = estimate(w, f, y, lab, none, u)
        if met(g, target) and g["method"] == "weight":
            break
        p = G.plan([g], target, Z, {G.ALL: top_rows(w, f, g, target)})["groups"][0]
        new = take(w, lab, u, p["draw"]) if p["draw"] else np.array([], int)
        if not len(new):
            break
        lab[new] = True
    return int(lab.sum()), estimate(w, f, y, lab, none, u)


def random_complaints(w, f, y, target, rng, step=25):
    """Complaints drawn at random (a text's chance grows with its copies), each draw labelling its text; PPI++ over
    the complaints, the drawn ones as the labelled sample. Returns the texts labelled."""
    rows = np.repeat(np.arange(len(w)), w.astype(int))
    order = rng.permutation(len(rows))
    fr, yr = f[rows], y[rows]
    for m in range(step, len(rows) + step, step):
        L = np.zeros(len(rows), bool)
        L[order[:m]] = True
        k = int(yr[L].sum())
        est = G.ppi_binary(int(L.sum()), k, int(fr[L].sum()), int((fr * yr)[L].sum()), int((~L).sum()), int(fr[~L].sum()))
        if est and (est["exact"] or (min(k, int(L.sum()) - k) >= MIN_EACH_SIDE and Z * est["se"] <= target)):
            break
    ci = (est["theta"] - Z * est["se"], est["theta"] + Z * est["se"]) if est else None
    return len(set(rows[order[:m]].tolist())), {"est": est, "ci": ci}


def allocation(w, f, y, target, trials, rng):
    """Labels used to reach ± target, and how often the final interval held the truth, for each way of drawing."""
    truth = float((w * y).sum() / w.sum())
    out = {}
    for name, fn in (("texts at random", random_texts), ("drawn by weight (the plan)", by_weight),
                     ("complaints at random", random_complaints)):
        used, held, done = [], 0, 0
        for _ in range(trials):
            n, g = fn(w, f, y, target, rng)
            used.append(n)
            if g["est"]:
                held += g["ci"][0] <= truth <= g["ci"][1]
                done += 1
        out[name] = {"labels": float(np.mean(used)), "labels_p90": float(np.percentile(used, 90)),
                     "coverage": held / done if done else None}
    return out


def heaviest(data):
    """The heaviest texts of the whole query: how much of it they hold, and the models' mistakes on them."""
    w = data["weights"]["all complaints"]
    order = np.argsort(-w)
    cum = np.cumsum(w[order]) / w.sum()
    out = {"held": {k: float(cum[k - 1]) for k in (1, 10, 40, 100)}, "texts": int(len(w)), "copies": int(w.sum()),
           "max": int(w.max()), "single": int((w == 1).sum()), "same_label": data["same_label"],
           "kish": float(w.sum() ** 2 / (w * w).sum()), "wrong": {}}
    for model, said in data["models"].items():
        wrong = said != data["truth"]
        out["wrong"][model] = {"top40": int(wrong[order[:40]].sum()), "texts": float(wrong.mean()),
                               "weight": float(w[wrong].sum() / w.sum())}
    return out


def run(trials=500, alloc_trials=100, seed=0):
    data = load()
    rng = np.random.default_rng(seed)
    res = {"rows": int(len(data["truth"])), "shard_rows": data["shard_rows"], "trials": trials,
           "alloc_trials": alloc_trials, "heaviest": heaviest(data), "queries": {}}
    for q, w in data["weights"].items():
        entry = {"texts": int((w > 0).sum()), "copies": int(w.sum()), "max": int(w.max()), "target": TARGETS[q],
                 "answers": {}}
        for ans in ANSWERS:
            y = (data["truth"] == ans).astype(int)
            a = {"truth": float((w * y).sum() / w.sum()), "texts_share": float(y[w > 0].mean()), "models": {}}
            for model, said in data["models"].items():
                f = (said == ans).astype(int)
                a["models"][model] = {"model_share": float((w * f).sum() / w.sum()),
                                      "coverage": coverage(w, f, y, trials, rng),
                                      "allocation": allocation(w[w > 0], f[w > 0], y[w > 0], TARGETS[q], alloc_trials, rng)}
            entry["answers"][ans] = a
        res["queries"][q] = entry
    os.makedirs(OUT, exist_ok=True)
    with open(f"{OUT}/results.json", "w") as fh:
        json.dump(res, fh, indent=2, default=float)
    with open(f"{OUT}/report.md", "w") as fh:
        fh.write(render(res))
    return OUT


def render(res):
    pct = lambda x: "–" if x is None else f"{100 * x:.1f} %"
    h = res["heaviest"]
    L = ["# Weighted shares and join fan-out, on real answers", "",
         f"No API calls. Generated by `jevbench weightvalidate`. The {res['rows']:,} judged CFPB narratives are "
         f"distinct texts; in the source shard ({res['shard_rows']:,} complaints) they are carried by "
         f"{h['copies']:,} complaints, from 1 to {h['max']:,} each ({h['single']:,} texts by one complaint only). "
         f"Each text's label and answer are taken to hold for every copy (the copies' own product agrees on "
         f"{pct(h['same_label'])} of them). Coverage: {res['trials']:,} draws per cell; labels needed: "
         f"{res['alloc_trials']} runs. Intervals at 95 %.", "",
         "## The heaviest texts", "",
         f"The heaviest text holds {pct(h['held'][1])} of all {h['copies']:,} complaints; the heaviest 10, "
         f"{pct(h['held'][10])}; 40, {pct(h['held'][40])}; 100, {pct(h['held'][100])}. Counted by weight, the "
         f"{h['texts']:,} texts are worth {h['kish']:.0f} equal ones (Kish's effective sample size).", "",
         "| predictor | texts it gets wrong | complaints it gets wrong (by weight) | wrong among the heaviest 40 |",
         "| --- | ---: | ---: | ---: |"]
    for m, x in h["wrong"].items():
        L.append(f"| {m} | {pct(x['texts'])} | {pct(x['weight'])} | {x['top40']} |")
    for q, e in res["queries"].items():
        L += ["", f"## {q.capitalize() if q == 'all complaints' else 'Complaints from the ' + q}", "",
              f"{e['texts']:,} texts carry {e['copies']:,} complaints (at most {e['max']:,} each). Target ± "
              f"{100 * e['target']:.0f} points.", ""]
        for ans, a in e["answers"].items():
            models = list(a["models"])
            L += [f"### Share about {ans}: {pct(a['truth'])} of the complaints, {pct(a['texts_share'])} of the texts",
                  "", "**1. Coverage.** Labels drawn at random among the texts: the weighted estimate (all draws; "
                  "the draws it calls reliable), the texts' own share and copies counted as labelled rows. Labels drawn "
                  "by weight: the first n texts in the ranking. ± is the mean half-width in points.", "",
                  "| predictor | labels | weighted | weighted, reliable | texts' share | copies as rows |",
                  "| --- | ---: | ---: | ---: | ---: | ---: |"]
            for m in models:
                c = a["models"][m]["coverage"]
                for n, r in c["random"].items():
                    cell = lambda x: (f"{pct(x['coverage'])} ±{100 * x['half_width']:.1f}" if x["coverage"] is not None
                                      else "–")
                    rel = r["reliable"]
                    rel_cell = (f"{pct(rel['coverage'])} (in {rel['draws']})" if rel["coverage"] is not None
                                else f"– (in 0)")
                    L.append(f"| {m} | {n} at random | {cell(r['weighted'])} | {rel_cell} | {cell(r['texts'])} | "
                             f"{cell(r['copies'])} |")
                for n, r in c["by weight"].items():
                    rel = (f"{pct(r['reliable_coverage'])} ({pct(r['reliable'])} of draws)"
                           if r["reliable_coverage"] is not None else "–")
                    L.append(f"| {m} | {n} by weight | {pct(r['coverage'])} ±{100 * r['half_width']:.1f} | {rel} | | |")
            L += ["", f"**2. Labels to reach ± {100 * e['target']:.0f} points** (mean, and 90th percentile; share of "
                  "final intervals that held the truth).", "",
                  "| predictor | texts at random | drawn by weight (the plan) | complaints at random |",
                  "| --- | ---: | ---: | ---: |"]
            for m in models:
                al = a["models"][m]["allocation"]
                L.append(f"| {m} | " + " | ".join(
                    f"{x['labels']:,.0f} (p90 {x['labels_p90']:,.0f}; {pct(x['coverage'])})" for x in al.values()) + " |")
            L.append("")
    return "\n".join(L) + "\n"
