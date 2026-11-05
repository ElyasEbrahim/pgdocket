"""Rare answers in weighted shares and with several models, on real answers (#31, no API calls).

#30 gave a rare answer's share by group a betting interval below the floor of 10 labelled rows with and without it.
Weighted shares (#26) and several models (#27–#29) had floors of their own; #31 gives them betting intervals too:

1. **Weighted shares from labels drawn by weight** (`estimate --weight`, `betting.sps`), on the CFPB join of #26: the
   2,000 complaint texts judged once, standing for the 21,980 complaints in the source shard that carry them word for
   word (each text weighs its copies). Coverage: the first n texts of a fresh ranking by u / w, many times: the normal
   interval above and below its floor of 5 rows drawn with and without the answer, the betting interval, and what
   pgdocket now reports. Rounds: `estimate --weight --target 0.02 --draw` round after round until met, the plan with the
   floor only and with betting.
2. **The same from labels drawn at random** (`betting.weighted`): coverage only, since the plan draws by weight.
3. **Several models for a weighted share** (`estimate --with 2 --weight`, #29): rounds on the texts repeated 500 times
   (about a million rows), the active version Jev at 20 rows per request or Laya, `--with` Jev one row per request.
4. **Several models by region** (`estimate --with 2 --by region --target 0.03`, #28): rounds on 100 million rows in the
   regions' proportions, each region's cheapest mix, with the floor only and with betting, for answers from 1.8 % of
   complaints (student loans) to 7 % (credit cards).
"""
import json
import math
import os

import numpy as np

from pgdocket import betting
from pgdocket import groups as G
from pgdocket import multi as M
from pgdocket import multiweight as MW
from pgdocket.estimate import norm_ppf

from . import multigroupvalidate as MGV
from . import multiweightvalidate as MWV
from . import weightvalidate as WV

OUT = "results/rare-validation"
MODELS = {"Jev": "Jev, 1 row per request", "Jev, 20 rows per request": "Jev, 20 rows per request",
          "Laya": "Laya, 1 row per request"}
JOIN_ANSWERS = ("credit reporting", "debt collection", "credit card")
REGION_ANSWERS = ("student loan", "mortgage", "credit card")
DRAWN_SIZES = (50, 100, 150, 250)
RANDOM_SIZES = (200, 400, 800)
TARGET = 0.02
NAN = np.nan
Z = norm_ppf(0.975)


def _summary(v):
    """Coverage, draws and mean half-width of [(held, half-width or None)]."""
    if not v:
        return None
    widths = [x for _, x in v if x is not None]
    return {"coverage": float(np.mean([h for h, _ in v])), "draws": len(v),
            "half_width": float(np.mean(widths)) if widths else None}


def by_weight(w, f, y, lab, u, use_betting):
    """`estimate --weight` on one group of rows from labels drawn by weight (the run at the top of the ranking), with
    a betting interval below the floor when `use_betting`."""
    g = WV.estimate(w, f, y, lab, np.zeros(len(w), bool), u)
    if (use_betting and g["method"] == "weight" and not g["reliable"] and not (g["est"] and g["est"]["exact"])
            and g["p_xi"] is not None):
        G.bet_drawn(g, G.run_rows(w, f, y, u, g["p_xi"]), Z)
    return g


def coverage_drawn(w, f, y, draws, rng):
    """The first n rows of a fresh ranking by u / w, `draws` times: how often each interval holds the weighted share."""
    truth = float((w * y).sum() / w.sum())
    out = {}
    for n in DRAWN_SIZES:
        rec = {k: [] for k in ("normal", "normal_above", "normal_below", "betting", "reported")}
        for _ in range(draws):
            u = rng.random(len(w))
            lab = np.zeros(len(w), bool)
            lab[np.argsort(u / w)[:n]] = True
            g = by_weight(w, f, y, lab, u, False)
            if g["method"] != "weight" or (g["est"] and g["est"]["exact"]):
                continue
            above = min(g["effective"]) >= G.DRAWN_EACH_SIDE
            r = G.run_rows(w, f, y, u, g["p_xi"])
            ci = betting.sps(r["y"], r["f"], r["w"], r["u"], w.sum(), (w * f).sum(), r["heavy"], r["powers"], g["p_xi"])
            bet = (False, None) if ci is None else (ci[0] <= truth <= ci[1], (ci[1] - ci[0]) / 2)
            rec["betting"].append(bet)
            if g["est"] is None:
                rec["normal"].append((False, None))
                rec["normal_below"].append((False, None))
                rec["reported"].append(bet)
                continue
            norm = (g["ci"][0] <= truth <= g["ci"][1], Z * g["est"]["se"])
            rec["normal"].append(norm)
            rec["normal_above" if above else "normal_below"].append(norm)
            rec["reported"].append(norm if above else bet)
        out[n] = {"below_floor": len(rec["normal_below"]) / max(1, len(rec["betting"])),
                  **{k: _summary(v) for k, v in rec.items()}}
    return {"truth": truth, "sizes": out}


def coverage_random(w, f, y, draws, rng):
    """n rows drawn at random, `draws` times: the weighted estimate where reliable and where not, and betting."""
    truth = float((w * y).sum() / w.sum())
    out = {}
    for n in RANDOM_SIZES:
        rec = {k: [] for k in ("reliable", "unreliable", "betting", "reported")}
        for _ in range(draws):
            idx = rng.permutation(len(w))[:n]
            lab = np.zeros(len(w), bool)
            lab[idx] = True
            g = WV.estimate(w, f, y, lab)
            ci = betting.weighted(y[idx], f[idx], w[idx], w.sum(), (w * f).sum(), len(w), w[~lab].max())
            bet = (False, None) if ci is None else (ci[0] <= truth <= ci[1], (ci[1] - ci[0]) / 2)
            rec["betting"].append(bet)
            if g["est"] is None or g["est"]["exact"]:
                rec["reported"].append(bet)
                continue
            norm = (g["ci"][0] <= truth <= g["ci"][1], Z * g["est"]["se"])
            rec["reliable" if g["reliable"] else "unreliable"].append(norm)
            rec["reported"].append(norm if g["reliable"] else bet)
        out[n] = {k: _summary(v) for k, v in rec.items()}
    return {"truth": truth, "sizes": out}


def _met(g, target):
    return g["est"] is not None and g["reliable"] and (g["est"]["exact"] or Z * g["est"]["se"] <= target)


def rounds_weight(w, f, y, target, use_betting, u, rounds=12):
    """What `estimate --weight --target --draw` and `label` do, round after round, on the ranking by u / w, with the
    plan that weighs betting or the floor only."""
    lab = np.zeros(len(w), bool)
    for _ in range(rounds):
        g = by_weight(w, f, y, lab, u, use_betting)
        if _met(g, target) and g["method"] == "weight":
            break
        p = G.plan([g], target, Z, {G.ALL: WV.top_rows(w, f, g, target)}, bet=use_betting)["groups"][0]
        new = WV.take(w, lab, u, p["draw"]) if p["draw"] else np.array([], int)
        if not len(new):
            break
        lab[new] = True
    return int(lab.sum()), by_weight(w, f, y, lab, u, use_betting)


def _paired(floor, bet):
    """Runs where betting took fewer labels than the floor alone on the same draws, and where it took more."""
    a, b = np.array(floor), np.array(bet)
    return {"fewer": float(np.mean(b < a)), "more": float(np.mean(b > a))}


def allocation_weight(w, f, y, target, runs, rng):
    """Rounds with the floor only and with betting, each run on the same ranking both ways."""
    truth = float((w * y).sum() / w.sum())
    seeds = rng.integers(2 ** 63, size=runs)
    out, per = {}, {}
    for name, use in (("floor only", False), ("with betting", True)):
        used, held, done, bets = [], 0, 0, 0
        for seed in seeds:
            n, g = rounds_weight(w, f, y, target, use, np.random.default_rng(seed).random(len(w)))
            used.append(n)
            if g["est"]:
                held += g["ci"][0] <= truth <= g["ci"][1] if not g["est"]["exact"] else True
            done += _met(g, target)
            bets += bool(g.get("interval"))
        per[name] = used
        out[name] = {"labels": float(np.mean(used)), "labels_p90": float(np.percentile(used, 90)),
                     "coverage": held / runs, "reached": done / runs, "betting_share": bets / runs}
    out["paired"] = _paired(per["floor only"], per["with betting"])
    return out


def rounds_with_weight(w, y, a, v, cost_v, seeds, use_betting):
    """`estimate --with 2 --weight --target --queue --draw` round after round on the texts repeated, as #29 ran it,
    with the floor only or with betting: one run per seed, its ranking drawn from the seed."""
    W, Y, A, Vv = (np.tile(x, MWV.ROUND_COPIES) for x in (w, y, a, v))
    truth = float((W * Y).sum() / W.sum())
    heavy = MWV._heavy(w, MWV.ROUND_COPIES)
    out = []
    for seed in seeds:
        u = np.random.default_rng(seed).random(len(W))
        r = u / W
        x0 = x1 = 0.0
        for done in range(MWV.ROUNDS + 1):
            lab, run = r < x0, r < x1
            s = MW.sums("all", W, A, np.where(run, Vv, NAN), np.where(lab, Y, NAN), u)
            e = MW.estimate(s, Z)
            if (use_betting and e and not e["reliable"] and not e["exact"] and e["labels"]
                    and math.isfinite(s["xi0"])):
                MW.bet({"s": s, "est": e}, G.run_rows(W, A, Y, u, s["xi0"]), Z)
            p = MW.plan(s, e, heavy, MWV.TARGET, Z, MWV.LABEL, cost_v, bet=use_betting)[0]
            if p["kind"] == "met" or done == MWV.ROUNDS:
                break
            step = MW.this_round(p)
            x0, x1 = max(x0, step["xi0"]), max(x1, step["xi1"])
        n_lab, n_run = int((r < x0).sum()), int((r < x1).sum())
        out.append({"labels": n_lab, "rows": n_run, "cost": MWV.LABEL * n_lab + cost_v * n_run, "rounds": done,
                    "held": bool(e["theta"] is not None and e["ci"][0] <= truth <= e["ci"][1]),
                    "half_width": (e["ci"][1] - e["ci"][0]) / 2 if e["ci"] else None,
                    "betting": bool(e.get("interval"))})
    hw = [o["half_width"] for o in out if o["half_width"] is not None]
    return {"labels": float(np.mean([o["labels"] for o in out])),
            "labels_p90": float(np.percentile([o["labels"] for o in out], 90)),
            "rows": float(np.mean([o["rows"] for o in out])), "cost": float(np.mean([o["cost"] for o in out])),
            "rounds": float(np.mean([o["rounds"] for o in out])), "coverage": float(np.mean([o["held"] for o in out])),
            "half_width": float(np.mean(hw)) if hw else None,
            "within": float(np.mean(np.array(hw) <= MWV.TARGET * 1.05)) if hw else None,
            "betting_share": float(np.mean([o["betting"] for o in out])), "per_run": [o["labels"] for o in out]}


def rounds_by_region(y, f1, f2, region, w2, seeds, use_betting, N=MGV.TABLES[-1]):
    """`estimate --with 2 --by region --target --queue --draw` in rounds on a table of N rows, as #28 ran it, each
    region's labels in the order drawn so that a region below the floor can get a betting interval (`use_betting`):
    one run per seed, its draws made from the seed."""
    regions = {g: np.where(region == g)[0] for g in MGV.REGIONS}
    frac = {g: len(i) / len(y) for g, i in regions.items()}
    truth = {g: float(y[i].mean()) for g, i in regions.items()}
    p1 = {g: float(f1[i].mean()) for g, i in regions.items()}
    vs = MGV._versions("active", w2)
    out = []
    for seed in seeds:
        rr = np.random.default_rng(seed)
        draws = {g: rr.choice(i, 400_000) for g, i in regions.items()}
        n0, m1 = dict.fromkeys(MGV.REGIONS, 0), dict.fromkeys(MGV.REGIONS, 0)
        for done in range(MGV.ROUNDS + 1):
            per = []
            for g in MGV.REGIONS:
                Ng = round(N * frac[g])
                st = MGV._stats(y, f1, f2, draws[g], n0[g], m1[g], Ng, p1[g])
                idx = draws[g][:n0[g]]
                lab = ((y[idx], np.c_[f1[idx], np.where(np.arange(n0[g]) < m1[g], f2[idx], NAN)])
                       if use_betting else None)
                per.append((g, M.together(st, Ng, [dict(v) for v in vs], Z, MGV.TARGET, MGV.LABEL, lab)))
            plans = {g: r["plan"][0] for g, r in per}
            if done == MGV.ROUNDS or not any(p["more_labels"] or any(p["more_rows"].values()) for p in plans.values()):
                break
            for g, p in plans.items():
                n0[g] = max(n0[g], p["labels"])
                m1[g] = max(m1[g], p["rows"].get("v2", 0))
        final = {g: r["estimate"] for g, r in per}
        out.append({"labels": sum(n0.values()), "rows": sum(m1.values()), "rounds": done,
                    "cost": MGV.LABEL * sum(n0.values()) + w2 * sum(m1.values()),
                    "held": {g: bool(e["theta"] is not None and e["ci"] and e["ci"][0] <= truth[g] <= e["ci"][1])
                             for g, e in final.items()},
                    "half_width": {g: (e["ci"][1] - e["ci"][0]) / 2 if e["ci"] else None for g, e in final.items()},
                    "betting": sum(bool(e.get("interval")) for e in final.values())})
    held = [h for o in out for h in o["held"].values()]
    widths = [x for o in out for x in o["half_width"].values() if x is not None]
    return {"labels": float(np.mean([o["labels"] for o in out])),
            "labels_p90": float(np.percentile([o["labels"] for o in out], 90)),
            "rows": float(np.mean([o["rows"] for o in out])), "cost": float(np.mean([o["cost"] for o in out])),
            "rounds": float(np.mean([o["rounds"] for o in out])), "coverage": float(np.mean(held)),
            "half_width": float(np.mean(widths)), "within": float(np.mean(np.array(widths) <= MGV.TARGET * 1.05)),
            "betting_regions": float(np.mean([o["betting"] for o in out])), "per_run": [o["labels"] for o in out]}


def run(draws=500, runs=100, seed=0, parts="1234"):
    rng = np.random.default_rng(seed)
    res = {"draws": draws, "runs": runs, "target": TARGET}
    if set(parts) != set("1234") and os.path.exists(f"{OUT}/results.json"):   # a rerun of some parts keeps the rest
        res = {**json.load(open(f"{OUT}/results.json")), **res}
    if set(parts) & set("12"):
        data = WV.load()
        w = data["weights"]["all complaints"]
        keep = w > 0
        w = w[keep]
        res["join"] = {"texts": int(len(w)), "copies": int(w.sum()), "max": int(w.max()),
                       "kish": float(w.sum() ** 2 / (w * w).sum()), "answers": {}}
        for ans in JOIN_ANSWERS:
            y = (data["truth"][keep] == ans).astype(float)
            entry = res["join"]["answers"][ans] = {"truth": float((w * y).sum() / w.sum()), "models": {}}
            for model, run_id in MODELS.items():
                f = (data["models"][run_id][keep] == ans).astype(float)
                m = entry["models"][model] = {"says": float((w * f).sum() / w.sum())}
                if "1" in parts:
                    m["drawn"] = coverage_drawn(w, f, y, draws, rng)
                    if model != "Jev, 20 rows per request":
                        m["rounds"] = allocation_weight(w, f, y, TARGET, runs, rng)
                if "2" in parts:
                    m["random"] = coverage_random(w, f, y, max(100, draws // 2), rng)
                print("join", ans, model, flush=True)
    if "3" in parts:
        data = MWV.load()
        w = data["w"]
        keep = w > 0
        w = w[keep]
        res["with_weight"] = {"cost": data["cost"], "setups": {}}
        v_all = {ans: (data["models"][MWV.BETTER][keep] == ans).astype(float) for ans in JOIN_ANSWERS}
        for key, active in MWV.SETUPS.items():
            S = res["with_weight"]["setups"][key] = {"active": active, "answers": {}}
            for ans in JOIN_ANSWERS:
                y = (data["truth"][keep] == ans).astype(float)
                a = (data["models"][active][keep] == ans).astype(float)
                seeds = rng.integers(2 ** 63, size=max(20, runs // 2))
                arms = {name: rounds_with_weight(w, y, a, v_all[ans], data["cost"], seeds, use)
                        for name, use in (("floor only", False), ("with betting", True))}
                paired = _paired(*(arms[k].pop("per_run") for k in ("floor only", "with betting")))
                S["answers"][ans] = {"truth": float((w * y).sum() / w.sum()), **arms, "paired": paired}
                print("with weight", key, ans, flush=True)
    if "4" in parts:
        data = MGV.load()
        region = data["region"]
        w2 = data["cost"][MGV.BETTER]
        res["by_region"] = {"rows": {g: int((region == g).sum()) for g in MGV.REGIONS}, "cost": w2, "setups": {}}
        for key, active in MGV.SETUPS.items():
            S = res["by_region"]["setups"][key] = {"active": active, "answers": {}}
            for ans in REGION_ANSWERS:
                y = (data["truth"] == ans).astype(float)
                f1 = (data["models"][active] == ans).astype(float)
                f2 = (data["models"][MGV.BETTER] == ans).astype(float)
                seeds = rng.integers(2 ** 63, size=max(20, runs // 2))
                arms = {name: rounds_by_region(y, f1, f2, region, w2, seeds, use)
                        for name, use in (("floor only", False), ("with betting", True))}
                paired = _paired(*(arms[k].pop("per_run") for k in ("floor only", "with betting")))
                S["answers"][ans] = {"share": {g: float(y[region == g].mean()) for g in MGV.REGIONS}, **arms,
                                     "paired": paired}
                print("by region", key, ans, flush=True)
    os.makedirs(OUT, exist_ok=True)
    with open(f"{OUT}/results.json", "w") as fh:
        json.dump(res, fh, indent=2, default=float)
    with open(f"{OUT}/report.md", "w") as fh:
        fh.write(render(res))
    return OUT


def render(res):
    pct = lambda x: "–" if x is None else f"{100 * x:.1f} %"
    pts = lambda x: "–" if x is None else f"±{100 * x:.1f}"
    cell = lambda c: "–" if not c else f"{pct(c['coverage'])} {pts(c['half_width'])}"
    saved = lambda a, b: f"{100 * (1 - b / a):.0f} %" if a else "–"
    L = ["# Rare answers in weighted shares and with several models", "",
         "No API calls. Generated by `jevbench rarevalidate` (#31). Below its floor, the usual interval of a weighted "
         "share or of an estimate from several models is now a betting interval, valid at any number of labels, and "
         "the plan takes whichever reaches the target first. Coverage: intervals holding the truth, and their mean "
         "half-width in points (95 % intervals). Rounds: each run starts from the same ranking or draws with the floor "
         "only and with betting, so the two differ only in the plan and the interval.", ""]
    j = res.get("join")
    if j:
        L += ["## 1. Weighted shares from labels drawn by weight (`estimate --weight`)", "",
              f"The CFPB join of #26: {j['texts']:,} complaint texts judged once, standing for the {j['copies']:,} "
              f"complaints in the source shard that carry them word for word (the heaviest {j['max']:,} times; Kish "
              f"effective size {j['kish']:.0f}). Labels: the first n texts of a fresh ranking by u / w, {res['draws']} "
              f"times per cell. Floor: {G.DRAWN_EACH_SIDE} rows drawn with and without the answer.", ""]
        for ans, a in j["answers"].items():
            L += [f"### {ans.capitalize()}: {pct(a['truth'])} of the complaints", "",
                  "| model | labels | below the floor | usual interval, below it | betting | what pgdocket reports |",
                  "| --- | ---: | ---: | ---: | ---: | ---: |"]
            for model, m in a["models"].items():
                for n, c in (m.get("drawn") or {"sizes": {}})["sizes"].items():
                    L.append(f"| {model} | {n} | {pct(c['below_floor'])} | {cell(c['normal_below'])} | "
                             f"{cell(c['betting'])} | {cell(c['reported'])} |")
            L.append("")
        L += [f"**Rounds** (`estimate --weight --target {res['target']} --draw`, until met; {res['runs']} runs each):",
              "", "| answer | model | labels, floor only (p90) | with betting (p90) | fewer | runs with fewer / more | "
              "intervals held | target reached | final interval by betting |",
              "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
        for ans, a in j["answers"].items():
            for model, m in a["models"].items():
                r = m.get("rounds")
                if r:
                    f0, b = r["floor only"], r["with betting"]
                    L.append(f"| {ans} | {model} | {f0['labels']:,.0f} ({f0['labels_p90']:,.0f}) | "
                             f"{b['labels']:,.0f} ({b['labels_p90']:,.0f}) | {saved(f0['labels'], b['labels'])} | "
                             f"{pct(r['paired']['fewer'])} / {pct(r['paired']['more'])} | "
                             f"{pct(f0['coverage'])} → {pct(b['coverage'])} | {pct(b['reached'])} | "
                             f"{pct(b['betting_share'])} |")
        L += ["", "## 2. Weighted shares from labels drawn at random", "",
              "The same join, n texts drawn at random. The usual interval is trusted only with 10 labelled rows each "
              "way counted by weight and no unlabelled row heavier than its half-width; betting allows for every row "
              "not yet drawn, which a heavy row makes wide.", "",
              "| answer | model | labels | usual, where trusted | usual, where flagged | betting |",
              "| --- | --- | ---: | ---: | ---: | ---: |"]
        for ans, a in j["answers"].items():
            for model, m in a["models"].items():
                for n, c in (m.get("random") or {"sizes": {}})["sizes"].items():
                    L.append(f"| {ans} | {model} | {n} | {cell(c['reliable'])} | {cell(c['unreliable'])} | "
                             f"{cell(c['betting'])} |")
        L.append("")
    ww = res.get("with_weight")
    if ww:
        L += ["## 3. Several models for a weighted share (`estimate --with 2 --weight`)", "",
              f"As #29 ran it: the texts repeated {MWV.ROUND_COPIES} times, ± {100 * MWV.TARGET:.0f} points, labels $1, "
              f"Jev one row per request ${ww['cost'] * 1e6:.2f} per million rows on a run of the ranking. Rounds until "
              "met.", "", "| active version | answer | labels, floor only (p90) | with betting (p90) | fewer | "
              "runs with fewer / more | cost | intervals held | half-width |",
              "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
        for key, S in ww["setups"].items():
            for ans, a in S["answers"].items():
                f0, b = a["floor only"], a["with betting"]
                L.append(f"| {S['active']} | {ans} ({pct(a['truth'])}) | {f0['labels']:,.0f} ({f0['labels_p90']:,.0f}) | "
                         f"{b['labels']:,.0f} ({b['labels_p90']:,.0f}) | {saved(f0['labels'], b['labels'])} | "
                         f"{pct(a['paired']['fewer'])} / {pct(a['paired']['more'])} | "
                         f"${f0['cost']:,.0f} → ${b['cost']:,.0f} | {pct(f0['coverage'])} → {pct(b['coverage'])} | "
                         f"{pts(f0['half_width'])} → {pts(b['half_width'])} |")
        L.append("")
    br = res.get("by_region")
    if br:
        L += ["## 4. Several models by region (`estimate --with 2 --by region`)", "",
              f"As #28 ran it: 100 million rows in the proportions of the 1,992 complaints from the four regions "
              f"({', '.join(f'{g} {n}' for g, n in br['rows'].items())}), each region to ± 3 points, labels $1, Jev "
              f"one row per request ${br['cost'] * 1e6:.2f} per million rows on a sample. Rounds until every region's "
              "plan asks for nothing more.", "",
              "| active version | answer (share by region) | labels, floor only (p90) | with betting (p90) | fewer | "
              "runs with fewer / more | intervals held | half-width | regions ending on betting |",
              "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
        for key, S in br["setups"].items():
            for ans, a in S["answers"].items():
                f0, b = a["floor only"], a["with betting"]
                shares = ", ".join(f"{100 * v:.1f}" for v in a["share"].values())
                L.append(f"| {S['active']} | {ans} ({shares} %) | {f0['labels']:,.0f} ({f0['labels_p90']:,.0f}) | "
                         f"{b['labels']:,.0f} ({b['labels_p90']:,.0f}) | {saved(f0['labels'], b['labels'])} | "
                         f"{pct(a['paired']['fewer'])} / {pct(a['paired']['more'])} | "
                         f"{pct(f0['coverage'])} → {pct(b['coverage'])} | {pts(f0['half_width'])} → "
                         f"{pts(b['half_width'])} | {b['betting_regions']:.1f} of 4 |")
        L.append("")
    return "\n".join(L) + "\n"
