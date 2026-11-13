"""Better bets for the audit: start fresh where a fall may begin? (#42, no API calls)

`pgdocket audit` (#23) bets against "precision >= target" one audited row at a time, with aGRAPA bets sized from the
last 40 audited rows plus a pseudo-observation at the target, capped so one row can at most halve the wealth
(`metrics.eprocess_below`). After a long good stretch the window still holds the good rows when a fall begins. Designs,
all valid by Ville's inequality since every bet uses earlier rows only:
- A, as now: aGRAPA, a 40-row window, a cap of half the wealth;
- W20 and W80: windows of 20 and 80 rows;
- C8: a 40-row window and a cap of 0.8;
- M: fresh starts, a mixture of A-processes started at each batch b with weight 1 / (b (b + 1)); the starts still to
  come keep their weight at wealth 1;
- H: half A, half M.
Scenarios, 20 audited rows a batch: simulated falls (targets 85 and 90 %, 7 points above the target before the change,
5, 10 or 15 points below after it, from batch 1, 4 or 11; 2,000 audits each) and the real CFPB stream of #23 with
falls injected from batch 6 (500 audits each). Detection within 40, 100 and 200 audited rows after the change (real:
40, 100 and by the end). False alarms: precision exactly at the target over 1,000 rows, and the real streams.

The rule, fixed in issue #42 before the first run: a design whose false alarms exceed 5 % anywhere is out; among the
rest, the smallest worst-case regret (the best design's detection rate minus its own) wins, within 1 point of it the
smaller mean regret. If A wins, nothing is built.
"""
import json
import math
import os

import numpy as np

from . import auditvalidate as AV

OUT = "results/audit-bets"
BAD = math.log(20)
BATCH = 20
DESIGNS = {"A": "aGRAPA, 40-row window (as now)", "W20": "a 20-row window", "W80": "an 80-row window",
           "C8": "a cap of 0.8", "M": "fresh starts at every batch", "H": "half A, half fresh starts"}


def agrapa_logw(X, target, start=0, window=40, cap=0.5):
    """log wealth after each row, for every audit (rows of X, 0/1), of `metrics.eprocess_below` started at row `start`:
    rows before it leave the wealth at 1, and the bets use rows since the start only."""
    X = np.asarray(X, float)
    R, T = X.shape
    c = np.concatenate([np.zeros((R, 1)), np.cumsum(X, axis=1)], axis=1)
    out = np.zeros((R, T))
    lw = np.zeros(R)
    for i in range(start, T):
        lo = max(start, i - window)
        m = i - lo
        s = c[:, i] - c[:, lo]
        n = m + 1
        mu = (target + s) / n
        var = (target * (1 - target) + s - 2 * mu * s + m * mu * mu) / n
        gap = target - mu
        lam = np.where(gap > 0, np.minimum(gap / (var + gap * gap), cap / (1 - target)), 0.0)
        lw = lw + np.log1p(lam * (target - X[:, i]))
        out[:, i] = lw
    return out


def fresh_logw(X, target, starts, window=40, cap=0.5):
    """log wealth of the mixture of A-processes started at each batch's first row (`starts`), weight 1 / (b (b + 1))
    for batch b = 1, 2, …; the starts still to come keep their weight at wealth 1."""
    X = np.asarray(X, float)
    R, T = X.shape
    w = np.array([1 / (b * (b + 1)) for b in range(1, len(starts) + 1)])
    acc = np.zeros((R, T))
    started = np.zeros(T)
    for wb, s in zip(w, starts):
        acc[:, s:] += wb * np.exp(agrapa_logw(X[:, s:], target, 0, window, cap))
        started[s:] += wb
    return np.log(acc + (1 - started))


def all_logw(X, target, starts):
    a = agrapa_logw(X, target)
    m = fresh_logw(X, target, starts)
    return {"A": a, "W20": agrapa_logw(X, target, window=20), "W80": agrapa_logw(X, target, window=80),
            "C8": agrapa_logw(X, target, cap=0.8), "M": m, "H": np.logaddexp(a, m) - math.log(2)}


def first_alarm(lw):
    hit = lw >= BAD
    return np.where(hit.any(1), hit.argmax(1), 10 ** 9)


def rates(X, target, starts, change, horizons):
    """Per design: false alarms before the change, and detection within each horizon after it (no earlier alarm)."""
    out = {}
    for d, lw in all_logw(X, target, starts).items():
        f = first_alarm(lw)
        out[d] = {"before": 100 * float(np.mean(f < change)),
                  **{f"within {h}": 100 * float(np.mean((f >= change) & (f < change + h))) for h in horizons}}
    return out


def simulated(rng, runs=2000):
    cells = {}
    for target in (0.85, 0.90):
        for drop in (0.05, 0.10, 0.15):
            for batch in (1, 4, 11):
                pre = (batch - 1) * BATCH
                p = np.r_[np.full(pre, target + 0.07), np.full(200, target - drop)]
                X = (rng.uniform(size=(runs, len(p))) < p).astype(float)
                cells[f"simulated · target {100 * target:.0f} % · {100 * drop:.0f} points below it · from batch {batch}"] = \
                    rates(X, target, list(range(0, len(p), BATCH)), pre, (40, 100, 200))
    return cells


def null_rates(rng, runs=2000):
    out = {}
    for target in (0.85, 0.90):
        X = (rng.uniform(size=(runs, 1000)) < target).astype(float)
        out[f"simulated · exactly at the target ({100 * target:.0f} %), 1,000 rows"] = {
            d: 100 * float(np.mean(first_alarm(lw) < 10 ** 9)) for d, lw in all_logw(X, target, list(range(0, 1000, BATCH))).items()}
    return out


def real_audits(stream, per_batch, rng, trials, inject=None):
    """Audits of a real stream (dict period -> list of right/wrong): `per_batch` drawn at random from each period, in
    time order, as `jevbench auditvalidate` draws them; `inject`: (first period, precision after)."""
    periods = sorted(stream)
    rows, starts, change = [], [], None
    for _ in range(trials):
        x, st, ch = [], [], None
        for b, p in enumerate(periods, start=1):
            pool = np.array(stream[p], float)
            if inject and b >= inject[0]:
                if ch is None:
                    ch = len(x)
                keep = inject[1] / pool.mean()
                pool = np.where(pool == 1, rng.uniform(size=len(pool)) < keep, 0.0)
            st.append(len(x))
            x += list(rng.choice(pool, min(per_batch, len(pool)), replace=False))
        rows.append(x)
        starts, change = st, ch
    return np.array(rows, float), starts, change


def real(rng, trials=500):
    target, k8s = AV.k8s_stream()
    t, cfpb = AV.cfpb_stream()
    cells, null = {}, {}
    for after in (0.80, 0.75, 0.70):
        X, starts, change = real_audits(cfpb, 20, rng, trials, inject=(6, after))
        r = rates(X, 0.85, starts, change, (40, 100, X.shape[1] - change))
        for d in r:                                    # by the end: every row after the change
            r[d]["by the end"] = r[d].pop(f"within {X.shape[1] - change}")
        cells[f"CFPB 2022–2023 (85 %) · a fall to {100 * after:.0f} % from batch 6"] = r
    for name, (tg, stream) in {"CFPB 2022–2023 (85 %), as it was": (0.85, cfpb),
                               "Kubernetes, the live decision (90 %), as it was": (target, k8s)}.items():
        X, starts, _ = real_audits(stream, 20, rng, 1000)
        null[name] = {d: 100 * float(np.mean(first_alarm(lw) < 10 ** 9)) for d, lw in all_logw(X, tg, starts).items()}
    return cells, null


def choose(cells, out_of):
    ds = [d for d in DESIGNS if d not in out_of]
    regret, where = {d: [] for d in ds}, {d: (-1.0, None) for d in ds}
    for name, cell in cells.items():
        for h in (k for k in cell["A"] if k != "before"):
            best = max(cell[d][h] for d in ds)
            for d in ds:
                r = best - cell[d][h]
                regret[d].append(r)
                if r > where[d][0]:
                    where[d] = (r, f"{name} · {h}")
    worst = {d: max(r) for d, r in regret.items()}
    mean = {d: float(np.mean(r)) for d, r in regret.items()}
    chosen = min((d for d in ds if worst[d] <= min(worst.values()) + 1.0), key=lambda d: mean[d])
    return chosen, worst, mean, {d: w[1] for d, w in where.items()}


def run(seed=42):
    rng = np.random.default_rng(seed)
    cells = simulated(rng)
    real_cells, real_null = real(rng)
    cells |= real_cells
    null = null_rates(rng) | real_null
    before = {k: {d: v[d]["before"] for d in DESIGNS} for k, v in cells.items()}
    out_of = {d for d in DESIGNS if any(v[d] > 5 for v in null.values()) or any(v[d] > 5 for v in before.values())}
    chosen, worst, mean, where = choose(cells, out_of)
    res = {"seed": seed, "designs": DESIGNS, "out": sorted(out_of), "chosen": chosen, "worst_regret": worst,
           "mean_regret": mean, "worst_where": where, "false_alarms": null, "cells": cells}
    os.makedirs(OUT, exist_ok=True)
    with open(f"{OUT}/results.json", "w") as f:
        json.dump(res, f, indent=2)
    with open(f"{OUT}/report.md", "w") as f:
        f.write(render(res))
    return OUT


def render(res):
    ds = list(res["designs"])
    L = ["# Better bets for the audit: start fresh where a fall may begin?", "",
         "No API calls. Generated by `jevbench auditbets` (#42). Designs, all valid by Ville's inequality:", ""]
    L += [f"- **{d}**: {v}" + (" (out)" if d in res["out"] else "") + (" (**chosen**)" if d == res["chosen"] else "")
          for d, v in res["designs"].items()]
    L += ["", "**The rule, fixed in issue #42 before the first run.** A design whose false alarms exceed 5 % anywhere is out. "
              "Among the rest, the smallest worst-case regret wins (the best design's detection rate minus its own, in every "
              "scenario and at every horizon); within 1 point of it, the smaller mean regret. If A wins, nothing is built.", "",
          f"**Chosen: {res['chosen']}** ({res['designs'][res['chosen']]}).", "",
          "| design | largest regret | mean regret | where the largest is |", "| --- | ---: | ---: | --- |"]
    for d in ds:
        if d in res["worst_regret"]:
            L.append(f"| {d} | {res['worst_regret'][d]:.1f} | {res['mean_regret'][d]:.2f} | {res['worst_where'][d]} |")
        else:
            L.append(f"| {d} | out | | |")
    L += ["", "## False alarms (%)", "", "| stream | " + " | ".join(ds) + " |", "| --- | " + " | ".join("---:" for _ in ds) + " |"]
    L += [f"| {k} | " + " | ".join(f"{v[d]:.1f}" for d in ds) + " |" for k, v in res["false_alarms"].items()]
    L += ["", "## Detection (%): alarms before the change, then within each horizon after it", ""]
    for name, cell in res["cells"].items():
        hs = list(cell["A"])
        L += [f"**{name}**", "", "| design | " + " | ".join(hs) + " |", "| --- | " + " | ".join("---:" for _ in hs) + " |"]
        L += [f"| {d} | " + " | ".join(f"{cell[d][h]:.1f}" for h in hs) + " |" for d in ds]
        L.append("")
    return "\n".join(L) + "\n"
