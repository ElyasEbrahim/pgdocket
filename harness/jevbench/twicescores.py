"""How should `--twice` read a row's three asks? (#39, no API calls)

`--twice` (#38) bets that a row's stored answer is the odd one out of its three asks, judged by the answers. A change
that moves scores but hardly any answer, like jev-1.14 in place of jev-1.13, shows no odd answers. Designs, each on
the same pairs of re-asks, with the share #37 chose for rows asked twice (half of the evidence budget that needs no
labels):
- A, as now: by answers. When exactly two of the three agree, the third is the odd one.
- B: by scores alone. The two closest top-answer scores make the pair.
- C: A and B as two bets, the share split between them.
- D: by answers when exactly two agree, otherwise by scores.
- E: by every option's probability. The two asks closest in total variation make the pair.
Each treats the three asks alike, so without a change the stored one is the odd one with chance 1/3.

Data, no API calls:
- CFPB: the 12 runs in the original batches (four from October, eight from #38). Each series draws its stored run
  at random, and its re-asks from the other eleven. The changes run forward, from three runs of each: shuffled
  batches and one row per request. Accuracy drops are injected.
- Kubernetes: the stored answers and two re-asks. The options without their descriptions and jev-1.14 were each asked
  once, so their roles are swapped, as in #37.

The rule, fixed in issue #39 before the first run:
1. A design is out if its series without a change alarm in more than 5 %.
2. A design is out if, on #38's six triples of real repeats, its pooled exact two-sided binomial test of "the stored
   one is the odd one" against a third rejects at 5 % (C: if either of its two tests does).
3. Among the rest, a design's regret is the best design's detection rate minus its own, at the first check after a
   change and by the last check, in every scenario and setting. The smallest worst-case regret wins; within 1 point of
   it, the smaller mean regret.
4. If A wins, nothing is built. Otherwise the winner replaces A in `--twice` with the same share.

`--forward` (#40): jev-1.14 was asked twice more, through the worker as `--twice` does, so its change runs the right way
round: the stored jev-1.13 answers, then two jev-1.14 asks drawn from its three. Fixed in issue #40 before the asks:
the forward triples and jev-1.14 against itself are reported, and #39's rule is applied again.
"""
import json
import math
import os

import numpy as np

from pgdocket import drift
from pgdocket.compare import sign_test
from pgdocket.spec import choice_vector, option_display

from . import twicerepeat as TR
from .db import connect
from .drifttwice import binom_two_sided, odd_answers, odd_scores
from .report import choice_probs, load_run

OUT = "results/drift-twice-scores"
OUT_FORWARD = "results/drift-twice-scores-forward"
JEV114 = "jev-1.14 in place of jev-1.13"
ALPHA, SHARE, TWICE = drift.ALPHA, drift.MODEL_SHARE, drift.TWICE_SHARE
DESIGNS = {"A": "by answers (as now)", "B": "by scores alone", "C": "answers and scores, two bets",
           "D": "by answers, else by scores", "E": "by every option's probability"}
NEVER = 10 ** 9
CFPB_ORIG = ["17 Oct", "18 Oct, first", "18 Oct, second", "19 Oct"] + [f"F{i}" for i in range(1, 9)]
CFPB_CHANGES = {"shuffled batches": ["19 Oct shuffled", "H1", "H2"],
                "one row per request": ["19 Oct one row per request", "B1", "B2"]}
TASKS = {"CFPB": {"labelled": 300, "anchors": 50, "twice": (50, 100), "checks": 18},
         "Kubernetes": {"labelled": 100, "anchors": 25, "twice": (25, 50), "checks": 12}}


def odd_tv(p0, p1, p2, tol=1e-9):
    """Per row, the place left out by the two asks closest in total variation over every option (0: the stored one);
    -1 when no pair is closer than both others."""
    tv = lambda x, y: 0.5 * np.abs(np.asarray(x, float) - np.asarray(y, float)).sum(-1)
    d12, d02, d01 = tv(p1, p2), tv(p0, p2), tv(p0, p1)
    out = np.full(d12.shape, -1)
    out[(d12 < d02 - tol) & (d12 < d01 - tol)] = 0
    out[(d02 < d12 - tol) & (d02 < d01 - tol)] = 1
    out[(d01 < d12 - tol) & (d01 < d02 - tol)] = 2
    return out


def odd_all(p0, p1, p2):
    """The odd one out of three asks (rows × options each) by every design's reading: {"A": …, "B": …, "D": …, "E": …}."""
    a = [x.argmax(-1) for x in (p0, p1, p2)]
    s = [x.max(-1) for x in (p0, p1, p2)]
    oa, ob = odd_answers(*a), odd_scores(*s)
    return {"A": oa, "B": ob, "D": np.where(oa >= 0, oa, ob), "E": odd_tv(p0, p1, p2)}


def name_of(label):
    return TR.OCT.get(label) or TR.FRESH[label]


# ---------------------------------------------------------------- data
def load_cfpb():
    labels = CFPB_ORIG + [x for runs in CFPB_CHANGES.values() for x in runs]
    probs = {}
    with connect() as conn:
        for lab in labels:
            _, exps, answers, truth = load_run(conn, name_of(lab))
            opts = exps["choice_base"]["options"]
            probs[lab] = {h: choice_probs(a, opts)[0] for h, a in answers["choice_base"].items()}
    keys = sorted(set.intersection(*(set(p) for p in probs.values())) & set(truth))
    V = lambda lab: np.array([probs[lab][h] for h in keys])
    return {"orig": np.stack([V(x) for x in CFPB_ORIG]), "forward": True,
            "changes": {c: np.stack([V(x) for x in runs]) for c, runs in CFPB_CHANGES.items()},
            "truth": np.array([opts.index(truth[h].product) if truth[h].product in opts else -1 for h in keys])}


def load_k8s(name="k8s_route", forward=False):
    """The stored answers and two re-asks of jev-1.13, and the two changes as asked once. `forward` (#40): jev-1.14 as
    its three asks (22 October, and the pair of 13 November the worker linked with pair_of), run forward."""
    with connect() as conn:
        specs = dict(conn.execute("SELECT v.version, v.spec FROM docket.version v JOIN docket.decision d ON d.id = "
                                  "v.decision_id WHERE d.name = %s", (name,)).fetchall())
        rows = conn.execute("""
            SELECT l.sig, jo.answer,
                   (SELECT array_agg(j.answer ORDER BY j.judged_at) FROM docket.judgment j WHERE j.decision_id = d.id
                      AND j.version = 1 AND j.row_key = c.row_key AND j.purpose = 'probe'),
                   j2.answer, j3.answer
            FROM docket.decision d
            JOIN docket.current c ON c.decision_id = d.id AND c.version = 1
            JOIN docket.judgment jo ON jo.id = c.judgment_id
            JOIN demo.k8s_live l ON l.number::text = c.row_key
            JOIN docket.current c2 ON c2.decision_id = d.id AND c2.version = 2 AND c2.row_key = c.row_key
            JOIN docket.judgment j2 ON j2.id = c2.judgment_id
            JOIN docket.current c3 ON c3.decision_id = d.id AND c3.version = 3 AND c3.row_key = c.row_key
            JOIN docket.judgment j3 ON j3.id = c3.judgment_id
            WHERE d.name = %s ORDER BY c.row_key""", (name,)).fetchall()
        pairs = dict((k, (a1, a2)) for k, a1, a2 in conn.execute("""
            SELECT j2.row_key, j1.answer, j2.answer FROM docket.judgment j2
            JOIN docket.judgment j1 ON j1.id = j2.pair_of
            JOIN docket.decision d ON d.id = j2.decision_id
            WHERE d.name = %s AND j2.version = 3 AND j2.purpose = 'probe'""", (name,)).fetchall()) if forward else {}
        keys = [r[0] for r in conn.execute("""
            SELECT c.row_key FROM docket.decision d
            JOIN docket.current c ON c.decision_id = d.id AND c.version = 1
            JOIN demo.k8s_live l ON l.number::text = c.row_key
            JOIN docket.current c2 ON c2.decision_id = d.id AND c2.version = 2 AND c2.row_key = c.row_key
            JOIN docket.current c3 ON c3.decision_id = d.id AND c3.version = 3 AND c3.row_key = c.row_key
            WHERE d.name = %s ORDER BY c.row_key""", (name,)).fetchall()]
    names = [o["name"] for o in specs[1]["options"]]
    assert all([o["name"] for o in specs[v]["options"]] == names for v in (2, 3))
    vec = lambda answers, v: np.array([choice_vector(a, option_display(specs[v])) for a in answers])
    task = {"orig": np.stack([vec([r[1] for r in rows], 1), vec([r[2][0] for r in rows], 1), vec([r[2][1] for r in rows], 1)]),
            "forward": False, "truth": np.array([names.index(r[0]) for r in rows]),
            "changes": {"the options without their descriptions": vec([r[3] for r in rows], 2)[None],
                        JEV114: vec([r[4] for r in rows], 3)[None]}}
    if forward:
        assert len(pairs) == len(keys) and all(k in pairs for k in keys)
        task["changes"][JEV114] = np.stack([task["changes"][JEV114][0], vec([pairs[k][0] for k in keys], 3),
                                            vec([pairs[k][1] for k in keys], 3)])
        task["forward_changes"] = {JEV114}
    return task


# ---------------------------------------------------------------- the series
def series(task, cfg, m2, change, share, start, runs, rng):
    """The first check (1, 2, …) at which each design alarms, per run (NEVER if it doesn't), with `m2` rows without labels
    asked twice a check. change: None, a name in task['changes'], or ('drop', points)."""
    O, truth = task["orig"], task["truth"]
    R, P, K = O.shape
    injected = isinstance(change, tuple)
    C = None if change is None or injected else task["changes"][change]
    half = (1 - SHARE) * ALPHA / 2
    t_acc, t_sc = math.log(1 / half), math.log(1 / ((1 - TWICE) * half))
    t_odd, t_two = math.log(1 / (TWICE * half)), math.log(2 / (TWICE * half))
    n, labelled, checks = cfg["anchors"], cfg["labelled"], cfg["checks"]
    out = {d: np.full(runs, NEVER) for d in DESIGNS}

    def pick2(pool, size):
        """Two distinct runs from `pool` per row."""
        pool = np.asarray(pool)
        i = rng.integers(0, len(pool), size)
        j = (i + rng.integers(1, len(pool), size)) % len(pool)
        return pool[i], pool[j]

    for t in range(runs):
        s = int(rng.integers(R)) if task["forward"] else 0          # the stored run
        others = np.array([r for r in range(R) if r != s])
        right0 = O[s].argmax(-1) == truth
        hit = rng.random(P) < (change[1] / right0.mean() if injected else share)
        wrong = (truth + rng.integers(1, K, P)) % K                  # the answer an injected drop gives
        lab = rng.permutation(P)[:labelled]
        unl = rng.permutation(np.setdiff1d(np.arange(P), lab))
        seen = np.zeros(P, bool)
        W = B = U = D = 0
        k_ = {d: 0 for d in "ABDE"}
        n_ = {d: 0 for d in "ABDE"}
        left = set(DESIGNS)
        for k in range(1, checks + 1):
            a = np.take(lab, np.arange((k - 1) * n, k * n), mode="wrap")
            p = np.take(unl, np.arange((k - 1) * m2, k * m2), mode="wrap")
            active = change is not None and k >= start
            # the anchors: one re-ask each
            va = O[rng.choice(others, len(a)), a]
            if active and injected:
                ans = va.argmax(-1)
                bad = hit[a] & (ans == truth[a])
                va = va.copy()
                top, w = ans[bad], wrong[a][bad]
                va[bad, top], va[bad, w] = va[bad, w], va[bad, top]
            elif active:
                on = hit[a]
                va = np.where(on[:, None], C[rng.integers(0, len(C), len(a)), a], va)
            # the rows asked twice: the stored ask and two re-asks
            i1, i2 = pick2(others, len(p))
            v0, v1, v2 = O[s, p], O[i1, p], O[i2, p]
            if active and injected:
                flip = hit[p] & right0[p]
                v1, v2 = v1.copy(), v2.copy()
                for v in (v1, v2):
                    top = v.argmax(-1)[flip]
                    w = wrong[p][flip]
                    rows = np.flatnonzero(flip)
                    v[rows, top], v[rows, w] = v[rows, w], v[rows, top]
            elif active and (task["forward"] or change in task.get("forward_changes", ())):
                on = hit[p]
                c1, c2 = pick2(np.arange(len(C)), len(p))
                v1, v2 = np.where(on[:, None], C[c1, p], v1), np.where(on[:, None], C[c2, p], v2)
            elif active:                                             # the roles swapped: the changed ask first
                on = hit[p]
                j1, j2 = pick2(np.arange(R), len(p))
                v0 = np.where(on[:, None], C[0, p], v0)
                v1, v2 = np.where(on[:, None], O[j1, p], v1), np.where(on[:, None], O[j2, p], v2)
            ra = va.argmax(-1) == truth[a]
            o = right0[a]
            worse, better = o & ~ra, ~o & ra
            diff = np.r_[va.max(-1) - O[s, a].max(-1), v1.max(-1) - v0.max(-1)]
            up, down = diff > 1e-9, diff < -1e-9
            rows = np.r_[a, p]
            fresh = ~seen[rows]
            seen[rows] = True
            W += int((worse & fresh[:len(a)]).sum()); B += int((better & fresh[:len(a)]).sum())
            U += int((up & fresh).sum()); D += int((down & fresh).sum())
            fp = fresh[len(a):]
            odd = odd_all(v0[fp], v1[fp], v2[fp])
            for d in "ABDE":
                k_[d] += int((odd[d] == 0).sum()); n_[d] += int((odd[d] >= 0).sum())
            budget = SHARE * ALPHA * 6 / (math.pi ** 2 * k * k) / 2
            base = (drift.sign_test_one_sided(int(worse.sum()), int(better.sum())) < budget
                    or sign_test(int(up.sum()), int(down.sum())) < budget
                    or drift.log_bets(W, B) >= t_acc or drift.log_bets(D, U, two_sided=True) >= t_sc)
            e = {d: drift.log_odd(k_[d], n_[d]) for d in "ABDE"}
            hits = {"A": e["A"] >= t_odd, "B": e["B"] >= t_odd, "C": e["A"] >= t_two or e["B"] >= t_two,
                    "D": e["D"] >= t_odd, "E": e["E"] >= t_odd}
            for d in list(left):
                if base or hits[d]:
                    out[d][t] = k
                    left.discard(d)
            if not left:
                break
    return out


def real_repeats():
    """Criterion 2: each design's pooled test on #38's six triples of real repeats (October stored, a fresh pair)."""
    triples = [(a, b, c) for a, b, c in TR.ORIGINAL + TR.OTHERS]
    labels = sorted({x for t in triples for x in t})
    probs = {}
    with connect() as conn:
        for lab in labels:
            _, exps, answers, _ = load_run(conn, name_of(lab))
            opts = exps["choice_base"]["options"]
            probs[lab] = {h: choice_probs(x, opts)[0] for h, x in answers["choice_base"].items()}
    keys = sorted(set.intersection(*(set(p) for p in probs.values())))
    V = {lab: np.array([probs[lab][h] for h in keys]) for lab in labels}
    per, pooled = {}, {d: [0, 0] for d in "ABDE"}
    for a, b, c in triples:
        odd = odd_all(V[a], V[b], V[c])
        per[f"{a}; {b}, {c}"] = {d: {"stored": int((o == 0).sum()), "of": int((o >= 0).sum())} for d, o in odd.items()}
        for d, o in odd.items():
            pooled[d][0] += int((o == 0).sum()); pooled[d][1] += int((o >= 0).sum())
    tests = {d: {"stored": k, "of": m, "share": k / m, "p": binom_two_sided(k, m)} for d, (k, m) in pooled.items()}
    tests["C"] = {"p": min(tests["A"]["p"], tests["B"]["p"]), "parts": ["A", "B"]}
    return {"per_triple": per, "tests": tests, "passes": {d: tests[d]["p"] >= 0.05 for d in DESIGNS}}


def forward_check(task):
    """#40, steps 1 and 2: the stored jev-1.13 answers against the two new jev-1.14 asks, and jev-1.14's ask of 22
    October against them. Of the rows with an odd one out, how often is it the first, by each reading?"""
    stored, (old, new1, new2) = task["orig"][0], task["changes"][JEV114]
    out = {}
    for what, first in (("forward: the stored jev-1.13 answers, then the two new jev-1.14 asks", stored),
                        ("jev-1.14 against itself: 22 October, then the two new asks", old)):
        odd = odd_all(first, new1, new2)
        out[what] = {d: {"first": int((o == 0).sum()), "of": int((o >= 0).sum()),
                         "p": binom_two_sided(int((o == 0).sum()), int((o >= 0).sum()))} for d, o in odd.items()}
    same = out["jev-1.14 against itself: 22 October, then the two new asks"]
    out["jev-1.14 changed itself"] = bool(same["A"]["p"] < 0.05 or same["B"]["p"] < 0.05)
    return out


def choose(cells, out_of):
    """Criterion 3 among the designs still in: the smallest worst-case regret, then the smaller mean regret."""
    ds = [d for d in DESIGNS if d not in out_of]
    regret = {d: [] for d in ds}
    where = {d: (-1.0, None) for d in ds}
    for name, cell in cells.items():
        if "no change" in name:
            continue
        for when in ("at", "end"):
            best = max(cell[d][when] for d in ds)
            for d in ds:
                r = best - cell[d][when]
                regret[d].append(r)
                if r > where[d][0]:
                    where[d] = (r, f"{name} · {'at its first check' if when == 'at' else 'by the last check'}")
    worst = {d: max(r) for d, r in regret.items()}
    mean = {d: float(np.mean(r)) for d, r in regret.items()}
    chosen = min((d for d in ds if worst[d] <= min(worst.values()) + 1.0), key=lambda d: mean[d])
    return chosen, worst, mean, {d: w[1] for d, w in where.items()}


def run(seed=39, runs=1000, forward=False):
    rng = np.random.default_rng(seed)
    loaded = {"CFPB": load_cfpb(), "Kubernetes": load_k8s(forward=forward)}
    checks = forward_check(loaded["Kubernetes"]) if forward else None
    if checks and checks["jev-1.14 changed itself"]:          # #40, step 2: the simulation is skipped
        os.makedirs(OUT_FORWARD, exist_ok=True)
        with open(f"{OUT_FORWARD}/results.json", "w") as f:
            json.dump({"forward_check": checks}, f, indent=2)
        return OUT_FORWARD
    cells, false_alarms = {}, {}
    for name, cfg in TASKS.items():
        task = loaded[name]
        runout = cfg["labelled"] // cfg["anchors"] + 1
        starts = (1, runout, (runout + cfg["checks"]) // 2 + 1)
        scen = [(None, 1.0, 1)] + [(c, s, k) for c in task["changes"] for s in (1.0, 0.5, 0.25) for k in starts]
        scen += [(("drop", x), 1.0, k) for x in (0.05, 0.10) for k in starts]
        for m2 in cfg["twice"]:
            for change, share, start in scen:
                fa = series(task, cfg, m2, change, share, start, runs * (4 if change is None else 1), rng)
                what = ("no change" if change is None else f"{100 * change[1]:.0f}-point accuracy drop injected"
                        if isinstance(change, tuple) else f"{change}, on {100 * share:.0f} % of rows")
                key = f"{name} · {m2} rows asked twice a check · {what} · from check {start}"
                cells[key] = {d: {"at": 100 * float(np.mean(f <= start)), "end": 100 * float(np.mean(f <= cfg["checks"]))}
                              for d, f in fa.items()}
                if change is None:
                    false_alarms[key] = {d: v["end"] for d, v in cells[key].items()}
            print(name, m2, "done", flush=True)
    repeats = real_repeats()
    out_of = {d for d in DESIGNS if any(v[d] > 5 for v in false_alarms.values())} | \
             {d for d, ok in repeats["passes"].items() if not ok}
    chosen, worst, mean, where = choose(cells, out_of)
    res = {"seed": seed, "runs": runs, "designs": DESIGNS, "out": sorted(out_of), "chosen": chosen, "worst_regret": worst,
           "mean_regret": mean, "worst_where": where, "false_alarms": false_alarms, "repeats": repeats, "cells": cells,
           "forward_check": checks}
    out = OUT_FORWARD if forward else OUT
    os.makedirs(out, exist_ok=True)
    with open(f"{out}/results.json", "w") as f:
        json.dump(res, f, indent=2)
    with open(f"{out}/report.md", "w") as f:
        f.write(render(res))
    return out


def render(res):
    ds = list(res["designs"])
    rep = res["repeats"]["tests"]
    fc = res.get("forward_check")
    L = ["# How should `--twice` read a row's three asks?" + (" With jev-1.14 run forward" if fc else ""), "",
         "No API calls. Generated by `jevbench twicescores" + (" --forward` (#40)" if fc else "` (#39)") + " from real asks. "
         "Designs, each on the same pairs of re-asks and with the share of the evidence budget #37 chose for rows asked "
         "twice:", ""]
    if fc:
        F = ["jev-1.14 was asked twice more on the 300 Kubernetes issues (13 November 2026, $0.026, through the worker as "
             "`--twice` does), so its change runs forward here: the stored jev-1.13 answers, then two jev-1.14 asks drawn "
             "from its three. Everything else is as in #39, with the same seed. Fixed in issue #40 before the asks.", "",
             "## The new asks, as triples", "",
             "| triple | reading | the first is the odd one | p against a third |", "| --- | --- | ---: | ---: |"]
        for what, by in fc.items():
            if isinstance(by, dict):
                F += [f"| {what} | {d}: {res['designs'][d]} | {by[d]['first']} of {by[d]['of']} "
                      f"({100 * by[d]['first'] / max(1, by[d]['of']):.1f} %) | {by[d]['p']:.3g} |" for d in "ABDE"]
        F += ["", "jev-1.14 changed itself between 22 October and 13 November: "
                  + ("yes, so the simulation was skipped." if fc["jev-1.14 changed itself"] else
                     "no (neither the answers nor the scores test rejected at 5 %), so the simulation ran."), "",
              "## The simulation", ""]
        L[2:2] = F
    L += [f"- **{d}**: {v}" + (" (out)" if d in res["out"] else "") + (" (**chosen**)" if d == res["chosen"] else "")
          for d, v in res["designs"].items()]
    L += ["", "**The rule, fixed in issue #39 before the first run.** A design is out if its series without a change alarm "
              "in more than 5 %, or if its pooled test on #38's six triples of real repeats rejects \"the stored one is the "
              "odd one a third of the time\" at 5 %. Among the rest, the smallest worst-case regret wins (the best design's "
              "detection rate minus its own, at the first check after a change and by the last check, in every scenario "
              "and setting); within 1 point of it, the smaller mean regret.", "",
          f"**Chosen: {res['chosen']}** ({res['designs'][res['chosen']]}). {res['runs']:,} series per scenario, "
          f"{4 * res['runs']:,} without a change.", "",
          "| design | real repeats: the stored one odd | p against a third | largest regret | mean regret | where the largest is |",
          "| --- | ---: | ---: | ---: | ---: | --- |"]
    for d in ds:
        t = rep[d]
        share = f"{t['stored']} of {t['of']} ({100 * t['share']:.1f} %)" if "share" in t else "A and B"
        L.append(f"| {d} | {share} | {t['p']:.3g} | " + (f"{res['worst_regret'][d]:.1f} | {res['mean_regret'][d]:.2f} | "
                                                          f"{res['worst_where'][d]} |" if d in res["worst_regret"] else "out | | |"))
    L += ["", "## False alarms (%) over series without a change", "", "| setting | " + " | ".join(ds) + " |",
          "| --- | " + " | ".join("---:" for _ in ds) + " |"]
    L += [f"| {k.split(' · no change')[0]} | " + " | ".join(f"{v[d]:.1f}" for d in ds) + " |" for k, v in res["false_alarms"].items()]
    L += ["", "## Detection rate (%) at the first check after the change / by the last check", "",
          "| scenario | " + " | ".join(ds) + " |", "| --- | " + " | ".join("---:" for _ in ds) + " |"]
    L += [f"| {k} | " + " | ".join(f"{v[d]['at']:.1f} / {v[d]['end']:.1f}" for d in ds) + " |"
          for k, v in res["cells"].items() if "no change" not in k]
    L += ["", "## Real repeats, per triple: the stored one odd, of the rows with an odd one", "",
          "| triple | " + " | ".join(d for d in "ABDE") + " |", "| --- | " + " | ".join("---:" for _ in "ABDE") + " |"]
    L += [f"| {k} | " + " | ".join(f"{v[d]['stored']} / {v[d]['of']}" for d in "ABDE") + " |"
          for k, v in res["repeats"]["per_triple"].items()]
    return "\n".join(L) + "\n"
