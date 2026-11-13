"""Can asking rows twice catch changes in no one direction? (#37, no API calls)

The model check compares a row's stored answer with one new answer, and one difference says nothing without a
direction: the same model asked again also answers differently now and then. Two new answers do say something. If
nothing changed, the stored answer and the two new ones are three draws from the same model, so when exactly two of
them agree, the odd one is the stored answer a third of the time. After a change the two new answers agree with each
other, and the stored one is the odd one far more often, whichever way the answers moved.

With `--twice`, each row without a label is asked twice. Evidence that never expires bets on "the stored answer is the
odd one" against one chance in three (`log_odd`), from each row's first pair of re-asks in a series, on a share c of
the half of the evidence budget that needs no labels; the scores evidence keeps the rest of that half, accuracy its own.

Designs, per task with N anchors a check and M calls a check on rows without labels:
- once: M rows asked once (#36);
- twice, half the rows: M / 2 rows asked twice, the same calls;
- twice: M rows asked twice;
- once, twice the rows: 2 M rows asked once, the same calls as "twice".
Data, from real asks: the 2,000 CFPB complaints of Phase 0 (the stored run and three repeats of it; shuffled batches
and one row per request as real changes) and the 300 Kubernetes issues of k8s_route (the stored answers and two
re-asks; the options without their descriptions and jev-1.14 as real changes). A change needs two re-asks made after
it, and each change was run once, so for rows asked twice the roles are swapped: the changed run stands for the stored
answer, and two runs of the original for the re-asks (the sign of their score's change is still counted new minus
stored). Injected accuracy drops turn right answers into another answer, the same in both re-asks.

Fixed before the first run (issue #37):
1. Without a change, an exact test of "the stored answer is the odd one" against a third must not reject at 5 % on
   either task's real repeats, and simulated series without a change must alarm in at most 5 % of runs. If this fails,
   asking twice is not built.
2. Adopt `--twice` if, at the same calls, rows asked twice catch shuffled batches (every row, from the first check) by
   the last check in at least half the runs, where rows asked once catch them in under 5 %.
3. Recommend it as the way to spend calls on rows without labels only if it also loses at most 5 points in every other
   scenario at the same calls, at the first check after a change and by the last check.
4. Its share c: of 20 %, a third and a half, the one with the smallest worst-case shortfall from the best share in any
   scenario and design; within 1 point of that, the one with the smaller mean shortfall.
5. The same test on scores (the two closest of the three scores make the pair) is reported for comparison.
Also reported, not part of the rule: the answers test with a floor (an alarm also needs the stored answer to be the odd
one on at least 2 % of the rows asked twice more than a new answer is), and a test of that margin itself (`log_margin`).

What came of it: 2 passed (100 % of series against 2.4 %) and simulated series without a change alarmed in at most
1.5 %, but on the real CFPB repeats one of the three ways to pair them rejected (the stored run the odd one in 43 of 93
rows, p = 0.011). The criterion did not say how to treat three pairings; read strictly it is not met, so asking twice
was not built. #38 settled it on twelve fresh runs (`jevbench twicerepeat`: the stored answer was the odd one on 165 of
486 rows, p = 0.77), and `--twice` is built, with the bet (`drift.log_odd`) and the share chosen here.
"""
import itertools
import json
import math
import os

import numpy as np

from pgdocket import drift
from pgdocket.compare import sign_test

from .db import connect
from .report import choice_probs, load_run

OUT = "results/drift-twice"
ALPHA, SHARE = drift.ALPHA, drift.MODEL_SHARE
SHARES = (0.2, 1 / 3, 0.5)
MIN_CHANGED = 0.02
NEVER = 10 ** 9
CFPB = {"stored": "jev-20261017-122314",
        "again": {"repeat of 18 Oct, first": "jev-20261018-110601", "repeat of 18 Oct, second": "jev-20261018-110607",
                  "repeat of 19 Oct": "jev-20261019-072124"},
        "changes": {"shuffled batches": "jev-20261019-072045", "one row per request": "jev-20261019-073856"}}
TASKS = {"CFPB: which product? 2,000 complaints, 300 labelled; a check asks 50 of them and spends 100 calls on rows "
         "without labels; 18 checks": ("cfpb", 300, 50, 100, 18),
         "Kubernetes: which SIG? 300 issues, 100 labelled; a check asks 25 of them and spends 50 calls on rows "
         "without labels; 12 checks": ("k8s", 100, 25, 50, 12)}
KINDS = ("answers", "answers, with the floor", "margin", "scores")


def designs(m):
    """Rows without labels asked a check, and whether each is asked twice."""
    return {"once": (m, False), "twice, half the rows": (m // 2, True), "twice": (m, True),
            "once, twice the rows": (2 * m, False)}


def odd_answers(a0, a1, a2):
    """Per row, the place of the odd answer when exactly two of the three agree (0: the stored one); -1 otherwise."""
    a0, a1, a2 = np.asarray(a0), np.asarray(a1), np.asarray(a2)
    out = np.full(len(a0), -1)
    out[(a1 == a2) & (a0 != a1)] = 0
    out[(a0 == a2) & (a1 != a0)] = 1
    out[(a0 == a1) & (a2 != a0)] = 2
    return out


def odd_scores(s0, s1, s2, tol=1e-9):
    """Per row, the place left out by the closest pair of the three scores (0: the stored one); -1 when no pair is
    closer than both others."""
    s0, s1, s2 = np.asarray(s0, float), np.asarray(s1, float), np.asarray(s2, float)
    d12, d02, d01 = np.abs(s1 - s2), np.abs(s0 - s2), np.abs(s0 - s1)
    out = np.full(len(s0), -1)
    out[(d12 < d02 - tol) & (d12 < d01 - tol)] = 0
    out[(d02 < d12 - tol) & (d02 < d01 - tol)] = 1
    out[(d01 < d12 - tol) & (d01 < d02 - tol)] = 2
    return out


def _mix(t):
    top = t.max(-1, keepdims=True)
    return (top + np.log(np.exp(t - top).mean(-1, keepdims=True)))[..., 0]


log_odd = drift.log_odd           # built into the model check (#38): the bet on the stored answer being the odd one


def log_margin(k, n, rows, margin=MIN_CHANGED):
    """log e-value against "the stored answer is the odd one on at most `margin` of the rows more than a new answer
    is". Per row asked twice, Z = 1 if the stored answer is the odd one, -1/2 if a new one is, 0 otherwise: its mean
    is 0 without a change, and the share of answers changed after one. Betting on a bounded mean: wealth times
    1 + lam (Z - margin), mixed over lam."""
    k, n, rows = (np.asarray(x, float)[..., None] for x in (k, n, rows))
    lam = drift.BETS / (0.5 + margin)
    return _mix(k * np.log1p(lam * (1 - margin)) + (n - k) * np.log1p(-lam * (0.5 + margin))
                + (rows - n) * np.log1p(-lam * margin))


def binom_two_sided(k, n, p=1 / 3):
    """Exact two-sided binomial test: the total probability of the counts no likelier than k."""
    if n == 0:
        return 1.0
    lg = lambda i: (math.lgamma(n + 1) - math.lgamma(i + 1) - math.lgamma(n - i + 1) + i * math.log(p)
                    + (n - i) * math.log(1 - p))
    pm = np.exp([lg(i) for i in range(n + 1)])
    return float(min(1.0, pm[pm <= pm[k] * (1 + 1e-9)].sum()))


# ---------------------------------------------------------------- data
def load_cfpb():
    ids = {"stored": CFPB["stored"], **CFPB["again"], **CFPB["changes"]}
    runs = {}
    with connect() as conn:
        for name, rid in ids.items():
            _, exps, answers, truth = load_run(conn, rid)
            opts = exps["choice_base"]["options"]
            runs[name] = {h: choice_probs(a, opts)[0] for h, a in answers["choice_base"].items()}
    keys = sorted(set.intersection(*(set(r) for r in runs.values())) & set(truth))
    arr = lambda name: (np.array([int(runs[name][h].argmax()) for h in keys]),
                        np.array([float(runs[name][h].max()) for h in keys]))
    return {"names": ["stored run (17 Oct)", *CFPB["again"]], "orig": [arr("stored"), *(arr(n) for n in CFPB["again"])],
            "truth": np.array([opts.index(truth[h].product) if truth[h].product in opts else -1 for h in keys]),
            "changes": {n: arr(n) for n in CFPB["changes"]}}


def load_k8s(name="k8s_route"):
    with connect() as conn:
        rows = conn.execute("""
            SELECT l.sig, jo.value, jo.score,
                   (SELECT array_agg(j.value ORDER BY j.judged_at) FROM docket.judgment j WHERE j.decision_id = d.id
                      AND j.version = 1 AND j.row_key = c.row_key AND j.purpose = 'probe'),
                   (SELECT array_agg(j.score ORDER BY j.judged_at) FROM docket.judgment j WHERE j.decision_id = d.id
                      AND j.version = 1 AND j.row_key = c.row_key AND j.purpose = 'probe'),
                   j2.value, j2.score, j3.value, j3.score
            FROM docket.decision d
            JOIN docket.current c ON c.decision_id = d.id AND c.version = 1
            JOIN docket.judgment jo ON jo.id = c.judgment_id
            JOIN demo.k8s_live l ON l.number::text = c.row_key
            JOIN docket.current c2 ON c2.decision_id = d.id AND c2.version = 2 AND c2.row_key = c.row_key
            JOIN docket.judgment j2 ON j2.id = c2.judgment_id
            JOIN docket.current c3 ON c3.decision_id = d.id AND c3.version = 3 AND c3.row_key = c.row_key
            JOIN docket.judgment j3 ON j3.id = c3.judgment_id
            WHERE d.name = %s ORDER BY c.row_key""", (name,)).fetchall()
    code = {v: i for i, v in enumerate(sorted({x for r in rows for x in (r[0], r[1], *r[3][:2], r[5], r[7])}))}
    arr = lambda vals, scores: (np.array([code[v] for v in vals]), np.array(scores, float))
    return {"names": ["stored answers (20 Oct)", "re-ask of 22 Oct, 03:52", "re-ask of 22 Oct, 11:50"],
            "orig": [arr([r[1] for r in rows], [r[2] for r in rows]), arr([r[3][0] for r in rows], [r[4][0] for r in rows]),
                     arr([r[3][1] for r in rows], [r[4][1] for r in rows])],
            "truth": np.array([code[r[0]] for r in rows]),
            "changes": {"the options without their descriptions": arr([r[5] for r in rows], [r[6] for r in rows]),
                        "jev-1.14 in place of jev-1.13": arr([r[7] for r in rows], [r[8] for r in rows])}}


# ---------------------------------------------------------------- the real triples
def real_triples(task, relabellings=5000, seed=37):
    """Which of three real asks is the odd one: the stored answers and each pair of repeats (no change), and each
    changed run in the stored answers' place with each pair of the original's runs after it."""
    names, orig = task["names"], task["orig"]

    def count(first, b, c):
        out = {}
        for kind, f, col in (("answers", odd_answers, 0), ("scores", odd_scores, 1)):
            o = f(first[col], b[col], c[col])
            k, n = int((o == 0).sum()), int((o >= 0).sum())
            out[kind] = {"stored": k, "of": n, "p": binom_two_sided(k, n)}
        return out
    pairs = list(itertools.combinations(range(1, len(orig)), 2))
    res = {"rows": len(orig[0][0]),
           "no change": {f"{names[0]}; then {names[b]} + {names[c]}": count(orig[0], orig[b], orig[c]) for b, c in pairs},
           "changes": {}}
    for ch, run in task["changes"].items():
        cells = [count(run, orig[b], orig[c]) for b, c in itertools.combinations(range(len(orig)), 2)]
        res["changes"][ch] = {kind: {"stored": sum(x[kind]["stored"] for x in cells), "of": sum(x[kind]["of"] for x in cells),
                                     "lowest": min(x[kind]["stored"] / max(1, x[kind]["of"]) for x in cells),
                                     "highest": max(x[kind]["stored"] / max(1, x[kind]["of"]) for x in cells),
                                     "pairs": len(cells), "largest_p": max(x[kind]["p"] for x in cells)}
                              for kind in ("answers", "scores")}
    if len(pairs) > 1:                     # several pairings: how often is the smallest of their p-values this small?
        A = np.array([o[0] for o in orig]).T
        V = A[~(A == A[:, :1]).all(1)]
        ps = lambda M: [binom_two_sided(int(((M[:, b] == M[:, c]) & (M[:, 0] != M[:, b])).sum()),
                                        int((odd_answers(M[:, 0], M[:, b], M[:, c]) >= 0).sum())) for b, c in pairs]
        pooled = lambda M: (sum(int(((M[:, b] == M[:, c]) & (M[:, 0] != M[:, b])).sum()) for b, c in pairs)
                            / max(1, sum(int((odd_answers(M[:, 0], M[:, b], M[:, c]) >= 0).sum()) for b, c in pairs)))
        obs, obs_pooled = min(ps(V)), pooled(V)
        rng = np.random.default_rng(seed)
        low = far = 0
        for _ in range(relabellings):      # without a change, a row's answers can be relabelled at random
            M = np.take_along_axis(V, np.argsort(rng.random(V.shape), axis=1), 1)
            low += min(ps(M)) <= obs + 1e-12
            far += abs(pooled(M) - 1 / 3) >= abs(obs_pooled - 1 / 3) - 1e-12
        res["pairings"] = {"smallest_p": obs, "p_of_smallest": low / relabellings, "pooled": obs_pooled,
                           "p_of_pooled": far / relabellings, "relabellings": relabellings}
    return res


# ---------------------------------------------------------------- the series
def series(task, labelled, n, m, checks, change, share, start, runs, rng):
    """Per design, per run and check, the statistics every rule's alarm reads. change: None, a name in
    task['changes'], or ('drop', points)."""
    A, S = np.array([o[0] for o in task["orig"]]), np.array([o[1] for o in task["orig"]])
    truth, P, R = task["truth"], A.shape[1], len(A) - 1
    right0 = A[0] == truth
    injected = isinstance(change, tuple)
    if injected:
        share = change[1] / right0.mean()               # a drop of d points: a share d / accuracy of rows turn wrong
    elif change is not None:
        CA, CS = task["changes"][change]
    ds = designs(m)
    out = {d: {k: (np.ones if k[0] == "p" else np.zeros)((runs, checks))
               for k in ("p_acc", "p_sc", "e_acc", "e_sc", "k_ans", "n_ans", "k_scr", "n_scr", "rows")} for d in ds}
    for t in range(runs):
        lab = rng.permutation(P)[:labelled]
        unl = np.setdiff1d(np.arange(P), lab)
        rng.shuffle(unl)
        hit = rng.random(P) < share                     # the rows the change reaches, fixed for the run
        for d, (per_check, twice) in ds.items():
            st = out[d]
            seen = np.zeros(P, bool)
            W = B = U = D = ka = na = ks = ns = N = 0
            for k in range(1, checks + 1):
                a = np.take(lab, np.arange((k - 1) * n, k * n), mode="wrap")
                p = np.take(unl, np.arange((k - 1) * per_check, k * per_check), mode="wrap")
                active = change is not None and k >= start
                ia = rng.integers(1, R + 1, len(a))                         # the anchors: one re-ask each
                right, diff_a = A[ia, a] == truth[a], S[ia, a] - S[0, a]
                if active and injected:
                    right = right & ~hit[a]
                elif active:
                    right, diff_a = np.where(hit[a], CA[a] == truth[a], right), np.where(hit[a], CS[a] - S[0, a], diff_a)
                worse, better = right0[a] & ~right, ~right0[a] & right
                i1 = rng.integers(1, R + 1, len(p))                         # rows without labels
                diff_p = S[i1, p] - S[0, p]
                if twice:
                    i2 = 1 + (i1 - 1 + rng.integers(1, R, len(p))) % R       # another of the repeats
                    t0, t1, t2, u0, u1, u2 = A[0, p], A[i1, p], A[i2, p], S[0, p], S[i1, p], S[i2, p]
                if active and injected and twice:
                    flip = hit[p] & right0[p]
                    t1, t2 = np.where(flip, -1, t1), np.where(flip, -1, t2)
                elif active and not injected and twice:                      # the roles swapped: the changed run first
                    on = hit[p]
                    j1 = rng.integers(0, R + 1, len(p))
                    j2 = (j1 + rng.integers(1, R + 1, len(p))) % (R + 1)
                    t0, t1, t2 = np.where(on, CA[p], t0), np.where(on, A[j1, p], t1), np.where(on, A[j2, p], t2)
                    u0, u1, u2 = np.where(on, CS[p], u0), np.where(on, S[j1, p], u1), np.where(on, S[j2, p], u2)
                    diff_p = np.where(on, CS[p] - S[j1, p], diff_p)
                elif active and not injected:
                    diff_p = np.where(hit[p], CS[p] - S[0, p], diff_p)
                diff = np.r_[diff_a, diff_p]
                up, down = diff > 1e-9, diff < -1e-9
                rows = np.r_[a, p]
                fresh = ~seen[rows]
                seen[rows] = True
                W += int((worse & fresh[:len(a)]).sum()); B += int((better & fresh[:len(a)]).sum())
                U += int((up & fresh).sum()); D += int((down & fresh).sum())
                if twice:
                    fp = fresh[len(a):]
                    oa, osc = odd_answers(t0, t1, t2)[fp], odd_scores(u0, u1, u2)[fp]
                    ka += int((oa == 0).sum()); na += int((oa >= 0).sum())
                    ks += int((osc == 0).sum()); ns += int((osc >= 0).sum()); N += int(fp.sum())
                i = k - 1
                st["p_acc"][t, i] = drift.sign_test_one_sided(int(worse.sum()), int(better.sum()))
                st["p_sc"][t, i] = sign_test(int(up.sum()), int(down.sum()))
                st["e_acc"][t, i], st["e_sc"][t, i] = drift.log_bets(W, B), drift.log_bets(D, U, two_sided=True)
                st["k_ans"][t, i], st["n_ans"][t, i], st["k_scr"][t, i], st["n_scr"][t, i], st["rows"][t, i] = ka, na, ks, ns, N
    return out


def first_alarm(st, c, kind="answers"):
    """The first check (1, 2, …) at which the model check alarms, per run (NEVER if it doesn't), when the odd-one-out
    test gets a share c of the label-free half of the evidence budget (c = 0: as in #36, without it)."""
    checks = st["p_acc"].shape[1]
    budget = SHARE * ALPHA * 6 / (math.pi ** 2 * np.arange(1, checks + 1) ** 2) / 2
    half = (1 - SHARE) * ALPHA / 2
    al = ((st["p_acc"] < budget) | (st["p_sc"] < budget) | (st["e_acc"] >= math.log(1 / half))
          | (st["e_sc"] >= math.log(1 / ((1 - c) * half))))
    if c > 0:
        thr = math.log(1 / (c * half))
        k, n = (st["k_scr"], st["n_scr"]) if kind == "scores" else (st["k_ans"], st["n_ans"])
        if kind == "margin":
            al |= log_margin(k, n, st["rows"]) >= thr
        else:
            hit = log_odd(k, n) >= thr
            if kind == "answers, with the floor":
                hit &= k - (n - k) / 2 >= MIN_CHANGED * st["rows"]
            al |= hit
    return np.where(al.any(1), al.argmax(1) + 1, NEVER)


def rates(stats, start, checks):
    """Per design and rule ('today', or a share and a kind of test): detection at the first check after the change
    and by the last check, in %."""
    out = {}
    for d, st in stats.items():
        cell = {"today": first_alarm(st, 0.0)}
        cell |= {f"{kind} · {c:.2f}": first_alarm(st, c, kind) for c in SHARES for kind in KINDS}
        out[d] = {r: {"at": 100 * float(np.mean(f <= start)), "end": 100 * float(np.mean(f <= checks))}
                  for r, f in cell.items()}
    return out


def choose(cells):
    """Criterion 4: per share, its shortfall from the best share in every scenario, design and moment."""
    key = lambda c: f"answers · {c:.2f}"
    regret = {c: [] for c in SHARES}
    where = {c: (-1.0, None) for c in SHARES}
    for name, cell in cells.items():
        if "no change" in name:
            continue
        for d, by_rule in cell.items():
            for when in ("at", "end"):
                best = max(by_rule[key(c)][when] for c in SHARES)
                for c in SHARES:
                    r = best - by_rule[key(c)][when]
                    regret[c].append(r)
                    if r > where[c][0]:
                        where[c] = (r, f"{name} · {d} · {'at its first check' if when == 'at' else 'by the last check'}")
    worst = {c: max(r) for c, r in regret.items()}
    mean = {c: float(np.mean(r)) for c, r in regret.items()}
    chosen = min((c for c in SHARES if worst[c] <= min(worst.values()) + 1.0), key=lambda c: mean[c])
    return chosen, worst, mean, {c: w[1] for c, w in where.items()}


def verdict(tasks, chosen):
    """The criteria of issue #37, from the results."""
    rule = f"answers · {chosen:.2f}"
    same, once = "twice, half the rows", "once"
    v = {"1_real_repeats": {}, "1_false_alarms": {}, "3_losses": []}
    for title, t in tasks.items():
        nc = t["triples"]["no change"]
        v["1_real_repeats"][title] = {k: x["answers"]["p"] for k, x in nc.items()}
        v["1_false_alarms"][title] = {d: {r: x["end"] for r, x in by.items()}
                                      for d, by in t["cells"]["no change · from check 1"].items()}
        for name, cell in t["cells"].items():
            if name.startswith("no change") or name.startswith("shuffled batches, on 100 % of rows · from check 1"):
                continue
            for when in ("at", "end"):
                loss = cell[once]["today"][when] - cell[same][rule][when]
                if loss > 5:
                    v["3_losses"].append({"task": title, "scenario": name, "when": when, "once": cell[once]["today"][when],
                                          "twice": cell[same][rule][when]})
    v["1_real_repeats_pass"] = all(p >= 0.05 for t in v["1_real_repeats"].values() for p in t.values())
    v["1_false_alarms_pass"] = all(x[r] <= 5 for t in v["1_false_alarms"].values() for x in t.values()
                                   for r in ("today", rule))
    cf = next(t for title, t in tasks.items() if title.startswith("CFPB"))
    sh = cf["cells"]["shuffled batches, on 100 % of rows · from check 1"]
    v["2"] = {"twice": sh[same][rule]["end"], "once": sh[once]["today"]["end"],
              "scores": sh[same][f"scores · {chosen:.2f}"]["end"]}
    v["2_pass"] = bool(v["2"]["twice"] >= 50 and v["2"]["once"] < 5)
    v["3_pass"] = not v["3_losses"]
    v["false_alarms_most"] = max(x[r] for t in v["1_false_alarms"].values() for x in t.values() for r in ("today", rule))
    v["reserving_costs"] = max(cell[d]["today"][when] - cell[d][rule][when] for t in tasks.values()
                               for name, cell in t["cells"].items() if "no change" not in name
                               for d in ("once", "once, twice the rows") for when in ("at", "end"))
    v["built"] = bool(v["1_real_repeats_pass"] and v["1_false_alarms_pass"] and v["2_pass"])
    return v


def run(seed=37, runs=1000):
    rng = np.random.default_rng(seed)
    loaded = {"cfpb": load_cfpb(), "k8s": load_k8s()}
    res = {"seed": seed, "runs": runs, "shares": list(SHARES), "tasks": {}}
    for title, (name, labelled, n, m, checks) in TASKS.items():
        task = loaded[name]
        runout = labelled // n + 1                       # the first check with no labelled row left to ask afresh
        starts = (1, runout, (runout + checks) // 2 + 1)
        scen = [(None, 1.0, 1)]
        scen += [(c, s, k) for c in task["changes"] for s in (1.0, 0.5, 0.25) for k in starts]
        scen += [(("drop", x), 1.0, k) for x in (0.05, 0.10) for k in starts]
        cells = {}
        for change, share, start in scen:
            st = series(task, labelled, n, m, checks, change, share, start, runs * (4 if change is None else 1), rng)
            what = ("no change" if change is None else f"{100 * change[1]:.0f}-point accuracy drop injected"
                    if isinstance(change, tuple) else f"{change}, on {100 * share:.0f} % of rows")
            cells[f"{what} · from check {start}"] = rates(st, start, checks)
        res["tasks"][title] = {"labelled": labelled, "anchors": n, "calls": m, "checks": checks, "labels_run_out": runout,
                               "triples": real_triples(task), "cells": cells}
        print(title, "done", flush=True)
    chosen, worst, mean, where = choose({f"{title.split(':')[0]} · {k}": v for title, t in res["tasks"].items()
                                         for k, v in t["cells"].items()})
    res |= {"chosen": chosen, "worst_shortfall": {f"{c:.2f}": x for c, x in worst.items()},
            "mean_shortfall": {f"{c:.2f}": x for c, x in mean.items()}, "worst_where": {f"{c:.2f}": x for c, x in where.items()}}
    res["verdict"] = verdict(res["tasks"], chosen)
    os.makedirs(OUT, exist_ok=True)
    with open(f"{OUT}/results.json", "w") as f:
        json.dump(res, f, indent=2)
    with open(f"{OUT}/report.md", "w") as f:
        f.write(render(res))
    return OUT


def render(res):
    c = res["chosen"]
    rule, v = f"answers · {c:.2f}", res["verdict"]
    pct = lambda x: f"{x:.1f}"
    ps = [p for t in v["1_real_repeats"].values() for p in t.values()]
    L = ["# Can asking rows twice catch changes in no one direction?", "",
         "No API calls. Generated by `jevbench drifttwice` (#37) from real asks. With `--twice` a row without a label is "
         "asked twice: of its stored answer and the two new ones, when exactly two agree, the odd one is the stored "
         "answer a third of the time if nothing changed. Evidence that never expires bets on it being the stored one, on "
         "a share of the half of the evidence budget that needs no labels. "
         f"{res['runs']:,} series per scenario, {4 * res['runs']:,} without a change.", "",
         "**Outcome.** " + ("Asking twice met the criteria fixed beforehand." if v["built"] else
                            "Asking twice is not built. It caught what no other check does (criterion 2), and simulated "
                            "series without a change stayed quiet, but criterion 1 is not met as written: on the real "
                            "CFPB repeats, one of the three ways to pair them rejects. The criterion did not say how to "
                            "treat three pairings, so the strict reading is applied. A replication on fresh repeats, "
                            "with one test fixed beforehand, would settle it.") + " #38 then did: on twelve fresh runs the "
         "stored answer was the odd one on 165 of 486 rows (p = 0.77 against a third, "
         "[report](../drift-twice-repeats/report.md)), and `--twice` is built as chosen here.", "",
         "## Which of three real asks is the odd one?", ""]
    for title, t in res["tasks"].items():
        tr = t["triples"]
        L += [f"### {title.split(';')[0]}", "", "Without a change, the stored answers first:", "",
              "| asks | rows with an odd answer | the stored one is it | p against a third | rows with an odd score | "
              "the stored one is it | p |", "| --- | ---: | ---: | ---: | ---: | ---: | ---: |"]
        for k, x in tr["no change"].items():
            a, s = x["answers"], x["scores"]
            L.append(f"| {k} | {a['of']} | {a['stored']} ({100 * a['stored'] / max(1, a['of']):.1f} %) | {a['p']:.3g} | "
                     f"{s['of']} | {s['stored']} ({100 * s['stored'] / max(1, s['of']):.1f} %) | {s['p']:.3g} |")
        if "pairings" in tr:
            q = tr["pairings"]
            L += ["", f"The smallest of these p-values is {q['smallest_p']:.3g}. When each row's answers are relabelled at "
                      f"random ({q['relabellings']:,} times), the smallest of the three is that small in "
                      f"{100 * q['p_of_smallest']:.1f} % of relabellings. Pooled over the pairings the stored run is the "
                      f"odd one in {100 * q['pooled']:.1f} %, as far from a third in {100 * q['p_of_pooled']:.1f} % of "
                      "relabellings."]
        L += ["", "With a change between the first ask and the two others (the changed run first, then two runs of the "
                  "original, every pair):", "",
              "| change | rows with an odd answer | the first is it | range over the pairs | rows with an odd score | "
              "the first is it | range |", "| --- | ---: | ---: | ---: | ---: | ---: | ---: |"]
        for ch, x in tr["changes"].items():
            a, s = x["answers"], x["scores"]
            L.append(f"| {ch} | {a['of'] / a['pairs']:.0f} | {100 * a['stored'] / max(1, a['of']):.1f} % | "
                     f"{100 * a['lowest']:.0f}–{100 * a['highest']:.0f} % | {s['of'] / s['pairs']:.0f} | "
                     f"{100 * s['stored'] / max(1, s['of']):.1f} % | {100 * s['lowest']:.0f}–{100 * s['highest']:.0f} % |")
        L.append("")
    L += ["## The criteria fixed before the first run", "",
          f"1. Real repeats: {sum(p < 0.05 for p in ps)} of the {len(ps)} tests above rejected at 5 % (smallest p = "
          f"{min(ps):.3g}): {'passed' if v['1_real_repeats_pass'] else 'not passed as written'}. Simulated series without a "
          f"change: at most {v['false_alarms_most']:.1f} % alarmed: {'passed' if v['1_false_alarms_pass'] else 'failed'}.",
          f"2. Shuffled batches on every row from the first check, by the last check, at the same calls: asked twice "
          f"{pct(v['2']['twice'])} %, asked once {pct(v['2']['once'])} %: {'passed' if v['2_pass'] else 'failed'}.",
          f"3. Every other scenario at the same calls: " + ("no loss above 5 points: passed." if v["3_pass"] else
                                                             f"{len(v['3_losses'])} losses above 5 points: failed, so "
                                                             "asking twice would be an option, not the default."),
          f"4. Share of the label-free half of the evidence budget: **{c:.2f}**. Reserving it costs rows asked once at "
          f"most {v['reserving_costs']:.1f} points in any scenario.",
          f"5. The same test on scores, half the rows asked twice: shuffled batches by the last check "
          f"{pct(v['2']['scores'])} %; the other scenarios are in the tables' last column.", "",
          "| share | largest shortfall | mean shortfall | where the largest is |", "| --- | ---: | ---: | --- |"]
    for s in res["shares"]:
        k = f"{s:.2f}"
        L.append(f"| {k}{' (chosen)' if s == c else ''} | {res['worst_shortfall'][k]:.1f} | {res['mean_shortfall'][k]:.2f} | "
                 f"{res['worst_where'][k]} |")
    if v["3_losses"]:
        L += ["", "Losses above 5 points at the same calls (asked once → half the rows asked twice):", ""]
        L += [f"- {x['task'].split(':')[0]}: {x['scenario']}, {'at its first check' if x['when'] == 'at' else 'by the last check'}: "
              f"{pct(x['once'])} → {pct(x['twice'])}" for x in v["3_losses"]]
    for title, t in res["tasks"].items():
        ds = list(designs(t["calls"]))
        L += ["", f"## {title}", "",
              f"The labels run out at check {t['labels_run_out']}. Detection rate (%) at the first check after the change / "
              f"by the last check. \"once\" is the check as it is (#36); the designs that ask twice use the answers test "
              f"on a share of {c:.2f}; the last three columns are half the rows asked twice under the other tests.", "",
              "| change | " + " | ".join(ds) + " | with the floor | margin of 2 % | scores |",
              "| --- | " + " | ".join("---:" for _ in range(len(ds) + 3)) + " |"]
        for k, cell in t["cells"].items():
            show = lambda d, r: f"{cell[d][r]['at']:.1f} / {cell[d][r]['end']:.1f}"
            L.append(f"| {k} | " + " | ".join(show(d, "today" if not designs(t["calls"])[d][1] else rule) for d in ds)
                     + " | " + " | ".join(show("twice, half the rows", f"{kind} · {c:.2f}") for kind in KINDS[1:]) + " |")
        L += ["", "False alarms (%) over the series without a change, by share:", "",
              "| design | as it is | " + " | ".join(f"answers, {s:.2f}" for s in res["shares"]) + " | "
              + " | ".join(f"scores, {s:.2f}" for s in res["shares"]) + " |",
              "| --- | " + " | ".join("---:" for _ in range(1 + 2 * len(res["shares"]))) + " |"]
        nc = t["cells"]["no change · from check 1"]
        for d in ds:
            L.append(f"| {d} | {nc[d]['today']['end']:.1f} | "
                     + " | ".join(f"{nc[d][f'answers · {s:.2f}']['end']:.1f}" for s in res["shares"]) + " | "
                     + " | ".join(f"{nc[d][f'scores · {s:.2f}']['end']:.1f}" for s in res["shares"]) + " |")
    return "\n".join(L) + "\n"
