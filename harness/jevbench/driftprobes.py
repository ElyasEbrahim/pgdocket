"""Should the model check re-ask rows without labels? (#36, no API calls)

Since #35 the model check's evidence comes from each labelled row's first re-ask in a series, so it stops growing once
every labelled row has been asked. Rows without labels can't show whether an answer got worse, but their scores can
show that the model changed: a row's stored score and its first re-ask are exchangeable without a change. With
`--probes M`, each check also re-asks M rows without labels, in rotation; they add to that check's scores test and to
the scores evidence. Accuracy stays with the labelled anchors. #35's split is kept (sign tests on 70 % of alpha spent
over the checks, evidence on 30 % once, half accuracy and half scores).

Designs, per task with N labelled anchors a check: the anchors alone; twice the anchors (the extra calls spent on
labelled rows); the anchors plus N, 2N or 4N probes. Data, from real re-asks:
- the 2,000 labelled CFPB complaints of Phase 0, 300 taken as labelled in each run, 50 anchors a check, 18 checks:
  three repeats of the stored run, and one row per request (scores up) and shuffled batches as real changes, on a share
  of rows; injected accuracy drops;
- the 300 labelled Kubernetes issues of k8s_route, 100 taken as labelled, 25 anchors a check, 12 checks: two re-asks,
  and the options without their descriptions (scores down) as a real change; injected accuracy drops.
Changes begin at the first check, once the labels have run out, and later.

The test, fixed before the first run: adopt probes if, at the same calls, the anchors plus as many probes catch changes
that begin after the labels have run out more often by the last check than twice the anchors, while false alarms stay
at most 5 % and the first check after a change loses at most 5 points against twice the anchors. On the complaints'
prototype it failed: at the same calls, probes trade accuracy for scores. So probes are added calls, not a swap, and
the report shows both comparisons.
"""
import json
import math
import os

import numpy as np

from pgdocket import drift
from pgdocket.compare import sign_test

from . import driftmodel as DM

OUT = "results/drift-probes"
ALPHA, SHARE = drift.ALPHA, drift.MODEL_SHARE
TASKS = {"CFPB: which product? 2,000 complaints, 300 labelled, 50 anchors a check, 18 checks": ("cfpb", 300, 50, 18),
         "Kubernetes: which SIG? 300 issues, 100 labelled, 25 anchors a check, 12 checks": ("k8s", 100, 25, 12)}


def designs(n):
    return {"anchors": (n, 0), "twice the anchors": (2 * n, 0), "anchors + as many probes": (n, n),
            "anchors + twice as many probes": (n, 2 * n), "anchors + 4× as many probes": (n, 4 * n)}


def series(task, labelled, n, checks, change, share, start, runs, rng):
    """The first check (1, 2, …) at which each design alarms, per run (a huge number if never)."""
    P = len(task["right0"])
    r0, s0, again = task["right0"], task["s0"], task["again"]
    ds = designs(n)
    out = {d: np.full(runs, 10 ** 9) for d in ds}
    if isinstance(change, tuple):
        share = change[1] / r0.mean()                  # a drop of d points: a share d / accuracy of rows turn wrong
    te = math.log(2 / ((1 - SHARE) * ALPHA))
    for t in range(runs):
        lab = rng.permutation(P)[:labelled]
        unl = np.setdiff1d(np.arange(P), lab)
        rng.shuffle(unl)
        hit = rng.random(P) < share

        def reask(rows, k):
            pick = rng.integers(0, len(again), len(rows))
            right = np.array([again[c][0][i] for c, i in zip(pick, rows)], bool)
            score = np.array([again[c][1][i] for c, i in zip(pick, rows)], float)
            if change is not None and k >= start:
                on = hit[rows]
                if isinstance(change, tuple):
                    right = right & ~on
                else:
                    nr, ns = task["changes"][change]
                    right, score = np.where(on, nr[rows], right), np.where(on, ns[rows], score)
            return right, score

        for d, (na, m) in ds.items():
            seen = np.zeros(P, bool)
            W = B = U = D = 0
            for k in range(1, checks + 1):
                a = np.take(lab, np.arange((k - 1) * na, k * na), mode="wrap")
                p = np.take(unl, np.arange((k - 1) * m, k * m), mode="wrap") if m else np.array([], int)
                rows = np.r_[a, p]
                right, score = reask(rows, k)
                ra = right[:len(a)]
                o = r0[a]
                worse, better = o & ~ra, ~o & ra
                diff = score - s0[rows]
                up, down = diff > 1e-9, diff < -1e-9
                fresh = ~seen[rows]
                seen[rows] = True
                W += int((worse & fresh[:len(a)]).sum()); B += int((better & fresh[:len(a)]).sum())
                U += int((up & fresh).sum()); D += int((down & fresh).sum())
                budget = SHARE * ALPHA * 6 / (math.pi ** 2 * k * k) / 2
                if (drift.sign_test_one_sided(int(worse.sum()), int(better.sum())) < budget
                        or sign_test(int(up.sum()), int(down.sum())) < budget
                        or drift.log_bets(W, B) >= te or drift.log_bets(D, U, two_sided=True) >= te):
                    out[d][t] = k
                    break
    return out


def run(seed=36, runs=500):
    rng = np.random.default_rng(seed)
    loaded = {"cfpb": DM.load_cfpb(), "k8s": DM.load_k8s()}
    res = {"seed": seed, "runs": runs, "tasks": {}}
    for title, (name, labelled, n, checks) in TASKS.items():
        task = loaded[name]
        runout = labelled // n + 1                       # the first check with no labelled row left to ask afresh
        starts = (1, runout, (runout + checks) // 2 + 1)
        real = list(task["changes"])
        changes = [None] + [(c, s) for c in real for s in (1.0, 0.5, 0.25)] + [(("drop", 0.05), 1.0), (("drop", 0.10), 1.0)]
        cells = {}
        for change, share in [(None, 1.0)] + changes[1:]:
            for start in ((1,) if change is None else starts):
                fa = series(task, labelled, n, checks, change, share, start, runs, rng)
                what = ("no change" if change is None else f"{100 * change[1]:.0f}-point accuracy drop injected"
                        if isinstance(change, tuple) else f"{change}, on {100 * share:.0f} % of rows")
                cells[f"{what} · from check {start}"] = {
                    d: {"at": 100 * float(np.mean(f <= start)), "end": 100 * float(np.mean(f <= checks))}
                    for d, f in fa.items()}
        res["tasks"][title] = {"labelled": labelled, "anchors": n, "checks": checks, "labels_run_out": runout,
                               "cells": cells, "verdict": verdict(cells, runout)}
        print(title, "done", flush=True)
    os.makedirs(OUT, exist_ok=True)
    with open(f"{OUT}/results.json", "w") as f:
        json.dump(res, f, indent=2)
    with open(f"{OUT}/report.md", "w") as f:
        f.write(render(res))
    return OUT


def verdict(cells, runout):
    """The fixed test (the same calls), and what adding probes did."""
    same, add = "anchors + as many probes", "anchors"
    late = {k: v for k, v in cells.items() if k != "no change · from check 1" and int(k.rsplit(" ", 1)[1]) >= runout}
    after = [k for k, v in cells.items() if k != "no change · from check 1"]
    return {
        "late_more_often": [k for k, v in late.items() if v[same]["end"] > v["twice the anchors"]["end"]],
        "late_less_often": [k for k, v in late.items() if v[same]["end"] < v["twice the anchors"]["end"]],
        "first_check_loss_vs_twice": max(v["twice the anchors"]["at"] - v[same]["at"] for k, v in cells.items()
                                         if k in after),
        "false_alarms": {d: v["end"] for d, v in cells["no change · from check 1"].items()},
        "adding_probes_worst_loss": max(max(v[add]["at"] - v[d]["at"], v[add]["end"] - v[d]["end"])
                                        for k, v in cells.items() if k in after
                                        for d in ("anchors + as many probes", "anchors + twice as many probes",
                                                  "anchors + 4× as many probes")),
    }


def render(res):
    L = ["# Should the model check re-ask rows without labels?", "",
         "No API calls. Generated by `jevbench driftprobes` (#36) from real re-asks. Each check re-asks N labelled "
         "anchors in rotation and, with `--probes M`, M rows without labels; the probes add to that check's scores test "
         "and to the scores evidence, not to accuracy. #35's split is kept. Detection rate (%) at the first check after "
         "the change, and by the last check.", "",
         "**The test, fixed before the first run:** at the same calls, do the anchors plus as many probes catch changes "
         "that begin after the labels have run out more often than twice the anchors, with false alarms at most 5 % and "
         "at most 5 points lost at the first check after a change? It failed: probes trade accuracy for scores. So "
         "probes are added calls, never a swap for anchors.", ""]
    for title, t in res["tasks"].items():
        ds = list(next(iter(t["cells"].values())).keys())
        v = t["verdict"]
        L += [f"## {title}", "",
              f"The labels run out at check {t['labels_run_out']}. Against twice the anchors (the same calls), the anchors "
              f"plus as many probes caught {len(v['late_more_often'])} late changes more often by the end and "
              f"{len(v['late_less_often'])} less often, and lost up to {v['first_check_loss_vs_twice']:.1f} points at "
              f"the first check after a change. Added to the anchors, probes never lost more than "
              f"{v['adding_probes_worst_loss']:.1f} points anywhere.", "",
              "| change | " + " | ".join(ds) + " |", "| --- | " + " | ".join("---:" for _ in ds) + " |"]
        for k, cell in t["cells"].items():
            L.append(f"| {k} | " + " | ".join(f"{cell[d]['at']:.1f} / {cell[d]['end']:.1f}" for d in ds) + " |")
        L.append("")
    return "\n".join(L) + "\n"
