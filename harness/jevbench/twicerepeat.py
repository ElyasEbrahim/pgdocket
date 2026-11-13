"""Do fresh repeat runs settle asking rows twice? (#38, no API calls: reads stored runs)

#37 tested asking rows without labels twice (`jevbench drifttwice`). Its first criterion, that without a change the
stored answer is the odd one out a third of the time, was not met as written on the October CFPB repeats (one of three
pairings rejected, p = 0.011). Issue #38 fixed one test before twelve fresh runs were made on 13 November 2026:

- Each October run stands for stored answers, followed by its own pair of fresh runs, in the same configuration: four
  runs in the original batches of 20, one in shuffled batches, one with one row per request.
- A triple counts only if both fresh runs disagree with its October run on at most 5 % of complaints (October's repeats
  disagreed with each other on 2.7-3.8 %, shuffled batches on 16.4 %). If no triple in the original batches counts,
  (F1; F2, F3) and (F4; F5, F6) take their place.
- Pooled over the triples that count, one exact two-sided binomial test of "the odd answer is the stored one" against a
  third, at 5 %. The criterion is met if it does not reject.
Reported, deciding nothing: every run's disagreement with every other, the changes forward (an October run in the
original batches stored, then two fresh runs in shuffled batches, or with one row per request), and the same test on
scores.
"""
import itertools
import json
import os

import numpy as np

from .db import connect
from .drifttwice import binom_two_sided, odd_answers, odd_scores
from .report import choice_probs, load_run

OUT = "results/drift-twice-repeats"
GATE = 0.05
OCT = {"17 Oct": "jev-20261017-122314", "18 Oct, first": "jev-20261018-110601", "18 Oct, second": "jev-20261018-110607",
       "19 Oct": "jev-20261019-072124", "19 Oct shuffled": "jev-20261019-072045",
       "19 Oct one row per request": "jev-20261019-073856"}
FRESH = {"F1": "jev-20261113-053938", "F2": "jev-20261113-053945", "F3": "jev-20261113-053950", "F4": "jev-20261113-053955",
         "F5": "jev-20261113-054001", "F6": "jev-20261113-054008", "F7": "jev-20261113-054013", "F8": "jev-20261113-054020",
         "H1": "jev-20261113-054035", "H2": "jev-20261113-054041", "B1": "jev-20261113-054137", "B2": "jev-20261113-054225"}
ORIGINAL = [("17 Oct", "F1", "F2"), ("18 Oct, first", "F3", "F4"), ("18 Oct, second", "F5", "F6"), ("19 Oct", "F7", "F8")]
OTHERS = [("19 Oct shuffled", "H1", "H2"), ("19 Oct one row per request", "B1", "B2")]
FALLBACK = [("F1", "F2", "F3"), ("F4", "F5", "F6")]
CONFIG = {**{k: "original batches" for k in ("17 Oct", "18 Oct, first", "18 Oct, second", "19 Oct")},
          **{f"F{i}": "original batches" for i in range(1, 9)}, "19 Oct shuffled": "shuffled batches",
          "H1": "shuffled batches", "H2": "shuffled batches", "19 Oct one row per request": "one row per request",
          "B1": "one row per request", "B2": "one row per request"}


def load():
    runs = {}
    with connect() as conn:
        for name, rid in {**OCT, **FRESH}.items():
            meta, exps, answers, _ = load_run(conn, rid)
            opts = exps["choice_base"]["options"]
            runs[name] = ({h: choice_probs(a, opts)[0] for h, a in answers["choice_base"].items()},
                          meta["started_at"].isoformat())
    keys = sorted(set.intersection(*(set(p) for p, _ in runs.values())))
    out = {n: (np.array([int(p[h].argmax()) for h in keys]), np.array([float(p[h].max()) for h in keys])) for n, (p, _) in runs.items()}
    return out, {n: t for n, (_, t) in runs.items()}, len(keys)


def triple(R, first, b, c):
    """Of the rows where exactly two answers agree (or one score is set apart), how often is it the first ask?"""
    out = {}
    for kind, f, col in (("answers", odd_answers, 0), ("scores", odd_scores, 1)):
        o = f(R[first][col], R[b][col], R[c][col])
        k, n = int((o == 0).sum()), int((o >= 0).sum())
        out[kind] = {"first": k, "second": int((o == 1).sum()), "third": int((o == 2).sum()), "of": n,
                     "p": binom_two_sided(k, n)}
    return out


def run():
    R, started, rows = load()
    names = list(R)
    differ = {a: {b: float(np.mean(R[a][0] != R[b][0])) for b in names} for a in names}
    gates, counted = {}, []
    for first, b, c in ORIGINAL + OTHERS:
        g = {b: differ[first][b], c: differ[first][c]}
        ok = all(v <= GATE for v in g.values())
        gates[f"{first}; {b}, {c}"] = {"disagreement": g, "counts": ok}
        if ok:
            counted.append((first, b, c))
    fallback = not any(t in counted for t in ORIGINAL)
    if fallback:
        counted = FALLBACK + counted
    per = {f"{a}; {b}, {c}": triple(R, a, b, c) for a, b, c in counted}
    K = sum(t["answers"]["first"] for t in per.values())
    N = sum(t["answers"]["of"] for t in per.values())
    Ks = sum(t["scores"]["first"] for t in per.values())
    Ns = sum(t["scores"]["of"] for t in per.values())
    test = {"stored_odd": K, "rows_with_an_odd_answer": N, "share": K / N if N else None, "p": binom_two_sided(K, N),
            "met": bool(binom_two_sided(K, N) >= 0.05), "fallback_used": fallback}
    scores = {"stored_odd": Ks, "rows_with_an_odd_score": Ns, "share": Ks / Ns if Ns else None, "p": binom_two_sided(Ks, Ns)}
    forward = {}
    for change, pair in (("shuffled batches", ("H1", "H2")), ("one row per request", ("B1", "B2"))):
        cells = {f"{o}; {pair[0]}, {pair[1]}": triple(R, o, *pair) for o, _, _ in ORIGINAL}
        k = sum(x["answers"]["first"] for x in cells.values())
        n = sum(x["answers"]["of"] for x in cells.values())
        ks = sum(x["scores"]["first"] for x in cells.values())
        ns = sum(x["scores"]["of"] for x in cells.values())
        forward[change] = {"triples": cells, "answers": {"first": k, "of": n, "share": k / n},
                           "scores": {"first": ks, "of": ns, "share": ks / ns},
                           "range": [min(x["answers"]["first"] / x["answers"]["of"] for x in cells.values()),
                                     max(x["answers"]["first"] / x["answers"]["of"] for x in cells.values())]}
    # deciding nothing: does the month show? same configuration, October against fresh, and each among itself
    month = {}
    for cfg in ("original batches", "shuffled batches", "one row per request"):
        o = [n for n in OCT if CONFIG[n] == cfg]
        f = [n for n in FRESH if CONFIG[n] == cfg]
        within = lambda xs: [differ[a][b] for a, b in itertools.combinations(xs, 2)]
        month[cfg] = {"october_vs_fresh": float(np.mean([differ[a][b] for a in o for b in f])),
                      "fresh_vs_fresh": float(np.mean(within(f))) if len(f) > 1 else None,
                      "october_vs_october": float(np.mean(within(o))) if len(o) > 1 else None}
    res = {"rows": rows, "runs": {**OCT, **FRESH}, "started": started, "gate": GATE, "gates": gates,
           "counted": [f"{a}; {b}, {c}" for a, b, c in counted], "per_triple": per, "test": test, "scores": scores,
           "forward": forward, "month": month, "disagreement": differ}
    os.makedirs(OUT, exist_ok=True)
    with open(f"{OUT}/results.json", "w") as fh:
        json.dump(res, fh, indent=2)
    with open(f"{OUT}/report.md", "w") as fh:
        fh.write(render(res))
    return OUT


def render(res):
    t, s = res["test"], res["scores"]
    pct = lambda x: f"{100 * x:.1f} %"
    L = ["# Do fresh repeat runs settle asking rows twice?", "",
         f"No API calls. Generated by `jevbench twicerepeat` (#38) from stored runs: six October runs of `choice_base` on the "
         f"{res['rows']:,} CFPB complaints, and twelve fresh ones made on 13 November 2026 (about $0.36). The test was fixed in "
         "issue #38 before any fresh run was made.", "",
         "**The test.** Each October run stands for stored answers, followed by its own pair of fresh runs in the same "
         "configuration. A triple counts only if both fresh runs disagree with its October run on at most "
         f"{100 * res['gate']:.0f} % of complaints. Pooled over the triples that count, one exact two-sided binomial test "
         "of \"the odd answer is the stored one\" against a third, at 5 %.", "",
         f"**Result: {'met' if t['met'] else 'not met'}.** The stored answer was the odd one on {t['stored_odd']} of the "
         f"{t['rows_with_an_odd_answer']} rows where exactly two of three answers agreed ({pct(t['share'])}), p = "
         f"{t['p']:.3g}." + (" No triple in the original batches counted, so (F1; F2, F3) and (F4; F5, F6) took their "
                              "place." if t["fallback_used"] else ""), "",
         "## The triples", "",
         "| stored; re-asks | fresh runs disagree with the stored run | counts | rows with an odd answer | the stored one | "
         "p against a third |", "| --- | --- | --- | ---: | ---: | ---: |"]
    for k, g in res["gates"].items():
        x = res["per_triple"].get(k)
        dis = ", ".join(f"{n} {pct(v)}" for n, v in g["disagreement"].items())
        L.append(f"| {k} | {dis} | {'yes' if g['counts'] else 'no'} | " + (
            f"{x['answers']['of']} | {x['answers']['first']} ({pct(x['answers']['first'] / max(1, x['answers']['of']))}) | "
            f"{x['answers']['p']:.3g} |" if x else "– | – | – |"))
    for k, x in res["per_triple"].items():
        if k not in res["gates"]:
            L.append(f"| {k} (in place of the original batches) | – | yes | {x['answers']['of']} | {x['answers']['first']} "
                     f"({pct(x['answers']['first'] / max(1, x['answers']['of']))}) | {x['answers']['p']:.3g} |")
    L += ["", f"Pooled: {t['stored_odd']} of {t['rows_with_an_odd_answer']}, p = {t['p']:.3g}. Only the pooled test decides.", "",
          "## Deciding nothing", "",
          f"**Scores.** On the same triples, where the two closest of three scores make the pair, the stored score was set "
          f"apart on {s['stored_odd']} of {s['rows_with_an_odd_score']} rows ({pct(s['share'])}), p = {s['p']:.3g}.", "",
          "**The month.** How often answers differ, on average, in each configuration:", "",
          "| configuration | October against fresh | fresh against fresh | October against October |",
          "| --- | ---: | ---: | ---: |"]
    for cfg, m in res["month"].items():
        f = lambda v: "–" if v is None else pct(v)
        L.append(f"| {cfg} | {f(m['october_vs_fresh'])} | {f(m['fresh_vs_fresh'])} | {f(m['october_vs_october'])} |")
    L += ["", "**The changes, forward.** An October run in the original batches stored, then two fresh runs in another "
              "configuration (#37 had to swap the roles):", "",
          "| change | rows with an odd answer | the stored one | range over the four October runs | rows with an odd score | "
          "the stored one |", "| --- | ---: | ---: | ---: | ---: | ---: |"]
    for ch, x in res["forward"].items():
        L.append(f"| {ch} | {x['answers']['of']} | {pct(x['answers']['share'])} | {100 * x['range'][0]:.0f}–"
                 f"{100 * x['range'][1]:.0f} % | {x['scores']['of']} | {pct(x['scores']['share'])} |")
    return "\n".join(L) + "\n"
