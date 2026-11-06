"""Can the model check of `pgdocket drift` collect evidence that never expires? (#35, no API calls)

Each model check re-asks labelled rows (anchors) and compares every re-ask with the row's stored answer: was it worse
(stored right, re-ask wrong) or better, and did its score go up or down? Until #35 the panel was fixed by the seed and
the k-th check of a series spent 0.05 * 6 / (pi^2 k^2). Designs compared, on the same scenarios:
- A: the fixed panel and that budget (#22);
- B: the fixed panel and conformal test martingales (Vovk): per anchor, the smoothed conformal p-value of its newest
  ask among all its asks (its stored answer first), bet on with a mixture of power calibrators and of how much to bet,
  on all of alpha (half on accuracy, a quarter on scores going down, a quarter on up);
- B_0.8: A on 80 % of alpha and B on the rest;
- C: a rotating panel: each check asks the next N labelled rows, in the seed's order, that the series has not asked
  yet (then those asked longest ago), on A's budget;
- D_w: C on a share w of alpha, plus evidence that never expires on the rest: each row counts once, at its first
  re-ask in the series, when its stored answer and the re-ask are two exchangeable draws, independent of every other
  row's. Where they differ, each sign is a fair coin, so a mixture of betting martingales on the signs
  (`drift.log_bets`: one-sided for accuracy, two-sided for scores) is valid however often it is read.
Data: the 300 labelled Kubernetes issues of k8s_route (stored answer and two real re-asks; jev-1.14, and the options
without their descriptions, as real changes) and the 2,000 labelled CFPB complaints of Phase 0 (a stored run and three
repeats of it; shuffled batches, and one row per request, as real changes). Re-asks without a change are drawn from a
row's real re-asks. A change reaches a share of the rows, fixed per run, from a given check on; an injected drop makes
right re-asks wrong on a share of the rows that lowers accuracy by that many points.

The rule, fixed before the first run: a design's regret in a scenario is the best design's detection rate there minus
its own, in points, at the first check after the change and by the last check. Choose the design whose largest regret
is smallest; within 1 point of that, the one with the smaller mean regret. False alarms are reported.
"""
import json
import math
import os

import numpy as np

from pgdocket import drift
from pgdocket.compare import sign_test

from . import driftvalidate as DV
from .db import connect
from .report import choice_probs, load_run

OUT = "results/drift-model"
ALPHA = drift.ALPHA
CASH = (0.05, 0.1, 0.2, 0.5)
WS = (0.5, 0.6, 0.7, 0.8, 0.9)
DESIGNS = ["A", "B", "B_0.8", "C"] + [f"D_{w}" for w in WS]
CFPB = {"stored": "jev-20261017-122314", "again": ["jev-20261018-110601", "jev-20261018-110607", "jev-20261019-072124"],
        "changes": {"shuffled batches": "jev-20261019-072045", "one row per request": "jev-20261019-073856"}}


def load_k8s():
    orig, ch = DV.load_anchors()
    return {"right0": orig[0], "s0": orig[1],
            "again": [ch["the same model asked again (first re-ask)"], ch["the same model asked again (second re-ask)"]],
            "changes": {"the options without their descriptions": ch["options without their descriptions"],
                        "jev-1.14 in place of jev-1.13": ch["jev-1.14 in place of jev-1.13"]}}


def load_cfpb():
    ids = [CFPB["stored"], *CFPB["again"], *CFPB["changes"].values()]
    runs = {}
    with connect() as conn:
        for rid in ids:
            _, exps, answers, truth = load_run(conn, rid)
            opts = exps["choice_base"]["options"]
            runs[rid] = ({h: choice_probs(a, opts)[0] for h, a in answers["choice_base"].items()}, opts, truth)
    keys = sorted(set.intersection(*(set(r[0]) for r in runs.values())) & set(runs[CFPB["stored"]][2]))

    def arr(rid):
        probs, opts, truth = runs[rid]
        return (np.array([opts[int(probs[h].argmax())] == truth[h].product for h in keys]),
                np.array([float(probs[h].max()) for h in keys]))
    right0, s0 = arr(CFPB["stored"])
    return {"right0": right0, "s0": s0, "again": [arr(r) for r in CFPB["again"]],
            "changes": {k: arr(r) for k, r in CFPB["changes"].items()}}


def calibrator(p):
    """A mixture of power calibrators, the integral over k in (0, 1) of k p^(k - 1): it integrates to 1 over p."""
    p = np.clip(p, 1e-300, 1 - 1e-12)
    L = np.log(p)
    return (p * L - p + 1) / (p * L * L)


def conformal_p(hist, new, rng):
    """Per anchor, the smoothed conformal p-value of `new` among its history and itself (larger is stranger)."""
    above = (hist > new[:, None]).sum(1)
    ties = (hist == new[:, None]).sum(1) + 1
    return (above + rng.random(len(new)) * ties) / (hist.shape[1] + 1)


def simulate(task, n, checks, change, share, start, runs, rng):
    """Per run and check, what every design needs. change: None, a name in task['changes'], or ('drop', points)."""
    P = len(task["right0"])
    r0, s0, again = task["right0"], task["s0"], task["again"]
    out = {k: np.ones((runs, checks)) for k in ("pA_acc", "pA_sc", "pC_acc", "pC_sc")}
    out |= {k: np.zeros((runs, checks)) for k in ("eD_acc", "eD_sc", "fresh")}
    ctm = {c: {k: np.zeros((runs, checks)) for k in ("acc", "down", "up")} for c in CASH}
    if isinstance(change, tuple):
        share = change[1] / r0.mean()                 # a drop of d points: a share d / accuracy of rows turn wrong
    for t in range(runs):
        order = rng.permutation(P)
        hit = rng.random(P) < share                   # the rows the change reaches, fixed for the run

        def reask(rows, k):
            pick = rng.integers(0, len(again), len(rows))
            right = np.array([again[c][0][i] for c, i in zip(pick, rows)])
            score = np.array([again[c][1][i] for c, i in zip(pick, rows)])
            if change is not None and k >= start:
                on = hit[rows]
                if isinstance(change, tuple):
                    right = right & ~on
                else:
                    nr, ns = task["changes"][change]
                    right, score = np.where(on, nr[rows], right), np.where(on, ns[rows], score)
            return right, score

        fixed = order[:n]
        hist_acc, hist_sc = (~r0[fixed]).astype(float)[:, None], s0[fixed][:, None]
        logw = {c: {"acc": 0.0, "down": 0.0, "up": 0.0} for c in CASH}
        seen = np.zeros(P, bool)
        W = B = U = D = 0
        for k in range(1, checks + 1):
            right, score = reask(fixed, k)                                  # A and B: the fixed panel
            o, diff = r0[fixed], score - s0[fixed]
            out["pA_acc"][t, k - 1] = drift.sign_test_one_sided(int((o & ~right).sum()), int((~o & right).sum()))
            out["pA_sc"][t, k - 1] = sign_test(int((diff > 1e-9).sum()), int((diff < -1e-9).sum()))
            new_acc = (~right).astype(float)
            ps = {"acc": conformal_p(hist_acc, new_acc, rng), "down": conformal_p(-hist_sc, -score, rng),
                  "up": conformal_p(hist_sc, score, rng)}
            for c in CASH:
                for key, p in ps.items():
                    logw[c][key] += float(np.log1p(-c + c * calibrator(p)).sum())
                    ctm[c][key][t, k - 1] = logw[c][key]
            hist_acc, hist_sc = np.c_[hist_acc, new_acc], np.c_[hist_sc, score]
            rows = np.take(order, np.arange((k - 1) * n, k * n), mode="wrap")  # C and D: the rotating panel
            right, score = reask(rows, k)
            o, diff = r0[rows], score - s0[rows]
            worse, better, up, down = o & ~right, ~o & right, diff > 1e-9, diff < -1e-9
            out["pC_acc"][t, k - 1] = drift.sign_test_one_sided(int(worse.sum()), int(better.sum()))
            out["pC_sc"][t, k - 1] = sign_test(int(up.sum()), int(down.sum()))
            first = ~seen[rows]
            seen[rows] = True
            W, B = W + int((worse & first).sum()), B + int((better & first).sum())
            U, D = U + int((up & first).sum()), D + int((down & first).sum())
            out["eD_acc"][t, k - 1] = drift.log_bets(W, B)
            out["eD_sc"][t, k - 1] = drift.log_bets(D, U, two_sided=True)
            out["fresh"][t, k - 1] = int(first.sum())
    out["ctm"] = ctm
    return out


def first_alarm(st, design):
    """The first check (1, 2, …) at which a design alarms, per run; a huge number if it never does."""
    runs, checks = st["pA_acc"].shape
    ks = np.arange(1, checks + 1)
    budget = ALPHA * 6 / (math.pi ** 2 * ks ** 2)

    def ctm_alarm(of):
        hit = np.zeros((runs, checks), bool)
        for key, part in (("acc", 0.5), ("down", 0.25), ("up", 0.25)):
            logs = np.array([st["ctm"][c][key] for c in CASH])
            top = logs.max(0)
            mix = top + np.log(np.exp(logs - top).mean(0))
            hit |= mix >= math.log(1 / (of * ALPHA * part))
        return hit
    if design == "A":
        al = (st["pA_acc"] < budget / 2) | (st["pA_sc"] < budget / 2)
    elif design == "B":
        al = ctm_alarm(1.0)
    elif design.startswith("B_"):
        w = float(design[2:])
        al = (st["pA_acc"] < w * budget / 2) | (st["pA_sc"] < w * budget / 2) | ctm_alarm(1 - w)
    elif design == "C":
        al = (st["pC_acc"] < budget / 2) | (st["pC_sc"] < budget / 2)
    else:
        w = float(design[2:])
        te = math.log(2 / ((1 - w) * ALPHA))
        al = ((st["pC_acc"] < w * budget / 2) | (st["pC_sc"] < w * budget / 2)
              | (st["eD_acc"] >= te) | (st["eD_sc"] >= te))
    return np.where(al.any(1), al.argmax(1) + 1, 10 ** 9)


def choose(scenarios):
    best = {k: max(v.values()) for k, v in scenarios.items()}
    worst = {d: max(best[k] - v[d] for k, v in scenarios.items()) for d in DESIGNS}
    mean = {d: float(np.mean([best[k] - v[d] for k, v in scenarios.items()])) for d in DESIGNS}
    where = {d: max(scenarios, key=lambda k: best[k] - scenarios[k][d]) for d in DESIGNS}
    chosen = min((d for d in DESIGNS if worst[d] <= min(worst.values()) + 1.0), key=lambda d: mean[d])
    return chosen, worst, mean, where


def run(seed=35, runs=500, checks=12):
    rng = np.random.default_rng(seed)
    tasks = {"Kubernetes: which SIG? 300 labelled issues, 50 a check": (load_k8s(), 50),
             "CFPB: which product? 2,000 labelled complaints, 100 a check": (load_cfpb(), 100)}
    scen, null, fresh = {}, {}, {}
    for title, (task, n) in tasks.items():
        for change in [None, *task["changes"], ("drop", 0.05), ("drop", 0.10)]:
            shares = (1.0,) if change is None or isinstance(change, tuple) else (1.0, 0.5, 0.25)
            for share in shares:
                for start in ((1,) if change is None else (1, checks // 2 + 1)):
                    st = simulate(task, n, checks, change, share, start, runs, rng)
                    fa = {d: first_alarm(st, d) for d in DESIGNS}
                    if change is None:
                        null[title] = {d: 100 * float(np.mean(f <= checks)) for d, f in fa.items()}
                        fresh[title] = [float(x) for x in st["fresh"].mean(0)]
                        continue
                    what = (f"{100 * change[1]:.0f}-point accuracy drop injected" if isinstance(change, tuple)
                            else f"{change}, on {100 * share:.0f} % of rows")
                    key = f"{title} · {what} · from check {start}"
                    scen[f"{key} · at check {start}"] = {d: 100 * float(np.mean(f <= start)) for d, f in fa.items()}
                    scen[f"{key} · by check {checks}"] = {d: 100 * float(np.mean(f <= checks)) for d, f in fa.items()}
        print(title, "done", flush=True)
    chosen, worst, mean, where = choose(scen)
    res = {"seed": seed, "runs": runs, "checks": checks, "designs": DESIGNS, "chosen": chosen, "worst_regret": worst,
           "mean_regret": mean, "worst_where": where, "scenarios": scen, "false_alarms": null, "fresh_rows": fresh}
    os.makedirs(OUT, exist_ok=True)
    with open(f"{OUT}/results.json", "w") as f:
        json.dump(res, f, indent=2)
    with open(f"{OUT}/report.md", "w") as f:
        f.write(render(res))
    return OUT


def render(res):
    ds = res["designs"]
    head = "| " + " | ".join(ds) + " |"
    rule = "| " + " | ".join("---:" for _ in ds) + " |"
    L = ["# Can the model check collect evidence that never expires?", "",
         "No API calls. Generated by `jevbench driftmodel` (#35) from real re-asks: the 300 labelled Kubernetes issues "
         "of `k8s_route` and the 2,000 labelled CFPB complaints of Phase 0. Designs:", "",
         "- **A**: a fixed panel of anchors; the k-th check spends 0.05 · 6 / (π² k²) (#22).",
         "- **B**: the fixed panel, with conformal test martingales on each anchor's asks, on all of the budget.",
         "- **B_0.8**: A on 80 % of the budget, B on the rest.",
         "- **C**: a rotating panel (each check asks labelled rows the series has not asked yet), on A's budget.",
         "- **D_w**: C on a share w of the budget, plus evidence that never expires from each row's first re-ask in "
         "the series, on the rest.", "",
         "**The rule, fixed before the first run.** A design's regret in a scenario is the best design's detection rate "
         "there minus its own, in points, at the first check after the change and by the last check. Choose the design "
         "whose largest regret is smallest; within 1 point of that, the one with the smaller mean regret.", "",
         f"**Chosen: {res['chosen']}.** {res['runs']} runs per scenario, {res['checks']} checks each.", "",
         "| design | largest regret | mean regret | where the largest is |", "| --- | ---: | ---: | --- |"]
    for d in ds:
        L.append(f"| {d}{' (chosen)' if d == res['chosen'] else ''} | {res['worst_regret'][d]:.1f} | "
                 f"{res['mean_regret'][d]:.2f} | {res['worst_where'][d]} |")
    L += ["", "## Detection rates (%)", "", "| scenario " + head, "| --- " + rule]
    L += [f"| {k} | " + " | ".join(f"{v[d]:.1f}" for d in ds) + " |" for k, v in res["scenarios"].items()]
    L += ["", f"## False alarms (%) over {res['checks']} checks without a change", "", "| task " + head, "| --- " + rule]
    L += [f"| {k} | " + " | ".join(f"{v[d]:.1f}" for d in ds) + " |" for k, v in res["false_alarms"].items()]
    L += ["", "## Rows asked for the first time in the series, per check (rotating panel)", ""]
    L += [f"- {k}: " + ", ".join(f"{x:.0f}" for x in v) for k, v in res["fresh_rows"].items()]
    return "\n".join(L) + "\n"
