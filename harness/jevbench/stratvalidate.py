"""Would labels stratified by the model's answer certify more answers on their own? (#23, no API calls)

`calibrate --per-answer` gives each answer its own precision guarantee, which needs enough labels on each answer.
Random labels give rare answers few. Stratifying by the answer (a uniform sample within each answer) keeps the
per-answer guarantee exact and can give every answer more labels. Each task's judged rows stand in for the
population, every label known; 400 label draws per cell:
- random: the labels pgdocket uses today;
- equal: every answer gets the same number of labels (all its rows if it has fewer);
- sqrt: labels in proportion to the square root of each answer's size, a compromise.
*Auto* is the share of all rows accepted with each certified answer's own threshold (90 % target); *ideal* uses every
label. A failure is a certified answer whose true precision above its threshold is below the target.
"""
import json
import math
import os

import numpy as np

from pgdocket import metrics as M

OUT = "results/stratified-labels"
DESIGNS = ("random", "equal", "sqrt")


def load_check(folder):
    rows = [json.loads(line) for line in open(f"results/{folder}/judgments.jsonl")]
    a = np.array([r["answer"]["choice"].split(":")[0] for r in rows])
    s = np.array([float(r["answer"]["probabilities"].get(r["answer"]["choice"], r["answer"]["confidence"])) for r in rows])
    return a, s, a == np.array([r["label"] for r in rows])


def load_phase0():
    from .db import connect
    from .report import choice_probs, load_run
    with connect() as conn:
        _, exps, answers, truth = load_run(conn, "jev-20261017-122314")
    opts = exps["choice_base"]["options"]
    a, s, y = [], [], []
    for h, ans in answers["choice_base"].items():
        if h in truth:
            v = choice_probs(ans, opts)[0]
            a.append(opts[int(v.argmax())])
            s.append(float(v.max()))
            y.append(truth[h].product)
    a = np.array(a)
    return a, np.array(s), a == np.array(y)


def allocate(sizes, budget, design):
    if design == "equal":                             # water-filling: equal shares, small answers take all their rows
        alloc, left = {k: 0 for k in sizes}, budget
        while left > 0 and any(alloc[k] < sizes[k] for k in sizes):
            open_ = sorted((k for k in sizes if alloc[k] < sizes[k]), key=lambda k: sizes[k])
            share = max(1, left // len(open_))
            for k in open_:
                add = min(share, sizes[k] - alloc[k], left)
                alloc[k] += add
                left -= add
        return alloc
    w = {k: math.sqrt(v) for k, v in sizes.items()}
    return {k: min(sizes[k], round(budget * w[k] / sum(w.values()))) for k in sizes}


def own_threshold(s, ok, target):
    c = M.precision_threshold(s, ok, target, 0.05)["chosen"]
    return c["threshold"] if c else None


def trial(a, s, ok, budget, design, rng, target):
    cats = sorted(set(a.tolist()))
    if design == "random":
        idx = rng.choice(len(a), budget, replace=False)
    else:
        al = allocate({c: int((a == c).sum()) for c in cats}, budget, design)
        idx = np.concatenate([rng.choice(np.flatnonzero(a == c), al[c], replace=False) for c in cats if al[c]])
    auto, fails, certified = 0, 0, 0
    for c in cats:
        m = a[idx] == c
        t = own_threshold(s[idx][m], ok[idx][m], target) if m.any() else None
        if t is not None:
            pop = (a == c) & (s >= t)
            auto += pop.sum()
            certified += 1
            fails += ok[pop].mean() < target
    return auto / len(a), certified, fails


def run(target=0.9, trials=400, seed=0):
    tasks = {"Kubernetes: which SIG? (500 issues)": (load_check("check-k8s-sig"), (150, 300)),
             "CFPB: which product? (500 complaints)": (load_check("check-cfpb-product"), (150, 300)),
             "CFPB: which product? (2,000 complaints)": (load_phase0(), (150, 300, 500))}
    res = {}
    for title, ((a, s, ok), budgets) in tasks.items():
        cats = sorted(set(a.tolist()))
        ideal = sum(((a == c) & (s >= t)).sum() for c in cats if (t := own_threshold(s[a == c], ok[a == c], target)) is not None)
        r = {"rows": len(a), "answers": len(cats), "accuracy": float(ok.mean()), "ideal_auto": ideal / len(a), "budgets": {}}
        for b in budgets:
            r["budgets"][b] = {}
            for design in DESIGNS:
                rng = np.random.default_rng(seed)
                runs = [trial(a, s, ok, b, design, rng, target) for _ in range(trials)]
                r["budgets"][b][design] = {"auto": float(np.mean([x[0] for x in runs])),
                                           "certified": float(np.mean([x[1] for x in runs])),
                                           "failures": float(sum(x[2] for x in runs) / max(1, sum(x[1] for x in runs)))}
        res[title] = r
    os.makedirs(OUT, exist_ok=True)
    with open(f"{OUT}/results.json", "w") as f:
        json.dump(res, f, indent=2, default=float)
    with open(f"{OUT}/report.md", "w") as f:
        f.write(render(res, target))
    return OUT


def render(res, target):
    pct = lambda x: f"{100 * x:.1f} %"
    L = ["# Would labels stratified by answer certify more answers?", "",
         f"No API calls. Generated by `jevbench stratvalidate`. Per-answer guarantees at {pct(target)} precision, 95 % "
         "confidence; 400 label draws per cell. *Auto*: share of all rows accepted. *Answers*: answers certified on "
         "average. *Failures*: certified answers whose true precision is below the target (5 % allowed).", ""]
    for title, r in res.items():
        L += [f"## {title}", "", f"{r['answers']} answers, accuracy {pct(r['accuracy'])}. With every label known, "
              f"{pct(r['ideal_auto'])} of rows could be accepted.", "",
              "| labels | " + " | ".join(f"{d}: auto · answers · failures" for d in DESIGNS) + " |",
              "| ---: | " + " | ".join("---:" for _ in DESIGNS) + " |"]
        for b, cells in r["budgets"].items():
            L.append(f"| {b} | " + " | ".join(f"{pct(c['auto'])} · {c['certified']:.1f} · {pct(c['failures'])}"
                                             for c in cells.values()) + " |")
        L.append("")
    return "\n".join(L)
