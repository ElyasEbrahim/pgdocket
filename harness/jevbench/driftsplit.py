"""Which share of the rows check's error budget should its window test get? (#34, no API calls)

The rows check of `pgdocket drift` (#33) runs two tests and alarms if either does: the recent window against the rows
from before the series began, on a share w of alpha spent over the checks (spent_alpha(w * alpha, k)), and evidence
that never expires over every row since, on the rest once (half on the answers, half on the scores). #33 used w = 0.5.

Every candidate share sees the same draws, from the scenarios of `jevbench driftvalidate`. Per check four statistics
are kept, so each candidate's alarms are other thresholds on them:
- p_mix: the smallest Bonferroni-adjusted p over answers whose recent share moved at least MIN_SHIFT (1 if none);
- p_ks: the KS p of the recent scores if their KS distance is at least MIN_KS (1 otherwise);
- s_ans, s_sc: the largest log e-value over items whose recent share moved at least the floor, minus log(#items).
A candidate alarms at check k when p_mix or p_ks < w * alpha * 6 / (pi^2 k^2) / 2, or s_ans or s_sc >=
-log((1 - w) * alpha / 2): what `drift.rows_check` does at w = drift.WINDOW_SHARE, as `verify` checks.

The rule, fixed before the first run: a candidate's regret in a scenario is the best candidate's power there minus its
own, in points. Choose the candidate whose largest regret is smallest; within 1 point of that, the one with the
smaller mean regret. The scenarios:
- series: the four tasks, 100 rows x 12 checks and 30 x 52, shifts of 10 and 15 points from the first check and of 20
  from halfway, each measured at the first check after the change began and by the end;
- first checks: every task's injected windows (as in `driftvalidate`), shifts of 10, 15 and 20 points.
False alarms and the real shifts by date are reported, not used to choose.
"""
import json
import math
import os
from collections import Counter

import numpy as np

from pgdocket import drift
from pgdocket import metrics as M

from . import driftvalidate as DV

OUT = "results/drift-split"
ALPHA = drift.ALPHA
WS = (0.5, 0.6, 0.7, 0.75, 0.8, 0.85, 0.9, 0.95, 1.0)
SHIFTS = ((0.0, 1), (0.10, 1), (0.15, 1), (0.20, "mid"))
DELTAS = (0.0, 0.10, 0.15, 0.20)


def window_stats(ref_a, ref_s, new_a, new_s):
    """(p_mix, p_ks) of `drift.windows`, with its effect floors applied."""
    tests, k = M.proportion_tests(Counter(ref_a), Counter(new_a))
    adj = [(c, x, y, min(1.0, p * k)) for c, x, y, p in tests]
    if k == 1:
        adj = [t for t in adj if t[0] == "yes"] or adj[:1]
    p_mix = min((t[3] for t in adj if abs(t[2] - t[1]) >= drift.MIN_SHIFT), default=1.0)
    d, p_ks = M.ks_2samp(ref_s, new_s)
    return p_mix, (p_ks if d >= drift.MIN_KS else 1.0)


def evidence_stat(ref, new, day, recent, kind, floor):
    """The largest log e-value of `drift.evidence` over items that moved by the floor, minus log(#items): its alarm at
    alpha is this >= log(1 / alpha)."""
    ref, new = np.asarray(ref), np.asarray(new)
    day, recent = np.asarray(day, int), np.asarray(recent, bool)
    if kind == "answers":
        items = sorted(set(ref.tolist()) | set(new.tolist()))
        if len(items) == 2:
            items = ["yes"] if "yes" in items else items[1:]
        has = lambda v, c: v == c
    else:
        items = sorted(set(np.quantile(ref, np.linspace(0.1, 0.9, 9)).tolist()))
        has = lambda v, c: v <= c
    D = int(day.max())
    blocks = np.bincount(day, minlength=D + 1)[1:]
    best = -math.inf
    for c in items:
        x = has(new, c).astype(float)
        k_ref = int(has(ref, c).sum())
        after = float(x[recent].mean()) if recent.any() else None
        if after is None or abs(after - k_ref / len(ref)) < floor:
            continue
        best = max(best, drift.log_evidence(k_ref, len(ref), np.bincount(day, weights=x, minlength=D + 1)[1:], blocks))
    return best - math.log(len(items))


def stats(ref_a, ref_s, new_a, new_s, day, j):
    """The four statistics of check j (the rows of block j are its recent window)."""
    recent = np.asarray(day) == j
    p_mix, p_ks = window_stats(ref_a, ref_s, [a for a, r in zip(new_a, recent) if r], np.asarray(new_s)[recent])
    return (p_mix, p_ks, evidence_stat(ref_a, new_a, day, recent, "answers", drift.MIN_SHIFT),
            evidence_stat(ref_s, new_s, day, recent, "scores", drift.MIN_KS))


def first_alarm(st, w):
    """The first check (1, 2, …) at which share w alarms, per run; a huge number if none. st: runs x checks x 4."""
    ks = np.arange(1, st.shape[1] + 1)
    a = w * ALPHA * 6 / (math.pi ** 2 * ks ** 2) / 2
    t = math.inf if w >= 1 else -math.log((1 - w) * ALPHA / 2)
    alarm = (st[:, :, 0] < a) | (st[:, :, 1] < a) | (st[:, :, 2] >= t) | (st[:, :, 3] >= t)
    return np.where(alarm.any(1), alarm.argmax(1) + 1, 10 ** 9)


def power(st, w, by):
    return 100 * float(np.mean(first_alarm(st, w) <= by))


def _draws(rows):
    labels = np.array([r[3] for r in rows])
    target = Counter(labels).most_common(2)[1][0]
    return labels, target, [r[1] for r in rows], np.array([r[2] for r in rows])


def series(rows, b, checks, delta, start, trials, rng, m=1000):
    """As `driftvalidate.series`: a reference of m rows and every check's b new rows drawn from the task's rows, one
    true answer (the second most common) delta more often from check `start` on. Returns (start, statistics)."""
    labels, target, A, S = _draws(rows)
    yes, no = np.flatnonzero(labels == target), np.flatnonzero(labels != target)
    base = len(yes) / len(rows)
    start = checks // 2 + 1 if start == "mid" else start
    out = np.ones((trials, checks, 4))
    for t in range(trials):
        ref = rng.integers(0, len(rows), m)
        ref_a, ref_s = [A[i] for i in ref], S[ref]
        new_a, new_s, day = [], [], []
        for j in range(1, checks + 1):
            k = rng.binomial(b, min(1.0, base + (delta if j >= start else 0.0)))
            pick = np.r_[rng.choice(yes, k), rng.choice(no, b - k)]
            rng.shuffle(pick)                                 # a check's rows come in no particular order
            new_a += [A[i] for i in pick]
            new_s += S[pick].tolist()
            day += [j] * b
            out[t, j - 1] = stats(ref_a, ref_s, new_a, new_s, day, j)
    return start, out


def injected(rows, m, delta, trials, rng):
    """As `driftvalidate.injected`: a first check of m rows against half of the rows, one answer delta more often."""
    labels, target, A, S = _draws(rows)
    out = []
    for _ in range(trials):
        idx = rng.permutation(len(rows))
        ref, pool = idx[: len(rows) // 2], idx[len(rows) // 2:]
        k = round(m * ((labels[ref] == target).mean() + delta))
        yes, no = pool[labels[pool] == target], pool[labels[pool] != target]
        if k > len(yes) or m - k > len(no):
            continue
        new = np.r_[rng.choice(yes, k, replace=False), rng.choice(no, m - k, replace=False)]
        out.append(stats([A[i] for i in ref], S[ref], [A[i] for i in new], S[new].tolist(), [1] * m, 1))
    return np.array(out)[:, None, :]


def verify(rows, rng, runs=25, b=60, checks=8, delta=0.12, w=None):
    """Check by check, the statistics at share w (default: drift.WINDOW_SHARE) alarm exactly when `drift.rows_check`
    does with that share. Returns (checks that agree, checks, alarms)."""
    w = drift.WINDOW_SHARE if w is None else w
    saved, drift.WINDOW_SHARE = drift.WINDOW_SHARE, w
    try:
        return _verify(rows, rng, runs, b, checks, delta, w)
    finally:
        drift.WINDOW_SHARE = saved


def _verify(rows, rng, runs, b, checks, delta, w):
    labels, target, A, S = _draws(rows)
    yes, no = np.flatnonzero(labels == target), np.flatnonzero(labels != target)
    base = len(yes) / len(rows)
    same = total = alarms = 0
    for _ in range(runs):
        ref = rng.integers(0, len(rows), 400)
        ref_a, ref_s = [A[i] for i in ref], S[ref]
        new_a, new_s, day = [], [], []
        for j in range(1, checks + 1):
            k = rng.binomial(b, min(1.0, base + (delta if j >= 3 else 0.0)))
            pick = np.r_[rng.choice(yes, k), rng.choice(no, b - k)]
            rng.shuffle(pick)
            new_a += [A[i] for i in pick]
            new_s += S[pick].tolist()
            day += [j] * b
            quiet = np.ones((1, j - 1, 4)) * [1.0, 1.0, -np.inf, -np.inf]     # checks before j: no alarm
            mine = bool(first_alarm(np.concatenate([quiet, [[stats(ref_a, ref_s, new_a, new_s, day, j)]]], 1), w)[0] == j)
            r = drift.rows_check(ref_a, ref_s.tolist(), new_a, new_s, day, [d == j for d in day], j)
            theirs = bool(r["mix"]["alarm"] or r["scores"]["alarm"])
            same += mine == theirs
            total += 1
            alarms += theirs
    return same, total, alarms


def choose(scenarios):
    """The rule: the smallest largest regret; within 1 point of it, the smallest mean regret."""
    best = {k: max(v.values()) for k, v in scenarios.items()}
    regret = {w: [best[k] - v[w] for k, v in scenarios.items()] for w in WS}
    worst = {w: max(r) for w, r in regret.items()}
    mean = {w: float(np.mean(r)) for w, r in regret.items()}
    where = {w: max(scenarios, key=lambda k: best[k] - scenarios[k][w]) for w in WS}
    chosen = min((w for w in WS if worst[w] <= min(worst.values()) + 1.0), key=lambda w: mean[w])
    return chosen, worst, mean, where


def run(seed=34, trials=500, series_trials=200):
    rng = np.random.default_rng(seed)
    checks = [verify(DV.load_check("check-k8s-sig"), np.random.default_rng(1), w=w) for w in (0.5, drift.WINDOW_SHARE)]
    assert all(s == t for s, t, _ in checks), checks
    scen, null, real = {}, {}, {}
    for title, (source, kind, sizes, days) in DV.TASKS.items():
        rows = DV.load_phase0() if source == "phase0" else DV.load_check(source)
        for m in sizes:
            for d in DELTAS:
                st = injected(rows, m, d, trials, rng)
                (null if d == 0 else scen)[f"first check · {title} · {m} rows · {100 * d:.0f} points"] = \
                    {w: power(st, w, 1) for w in WS}
        for b, c in DV.SCHEDULES:
            for d, s in SHIFTS:
                start, st = series(rows, b, c, d, s, series_trials, rng)
                name = f"series · {title} · {c} checks of {b} rows"
                if d == 0:
                    null[f"{name} · no shift"] = {w: power(st, w, c) for w in WS}
                    continue
                shift = f"{100 * d:.0f} points from check {start}"
                scen[f"{name} · {shift} · at check {start}"] = {w: power(st, w, start) for w in WS}
                scen[f"{name} · {shift} · by check {c}"] = {w: power(st, w, c) for w in WS}
        dates = DV.dates_for(kind, [r[0] for r in rows])
        keep = [i for i, r in enumerate(rows) if dates.get(r[0]) is not None]
        cut = max(dates[rows[i][0]] for i in keep) - DV.timedelta(days=days)
        new = [i for i in keep if dates[rows[i][0]] >= cut]
        ref = [i for i in keep if dates[rows[i][0]] < cut]
        st = np.array([[stats([rows[i][1] for i in ref], [rows[i][2] for i in ref], [rows[i][1] for i in new],
                              [rows[i][2] for i in new], [1] * len(new), 1)]])
        real[title] = {"rows": [len(ref), len(new)], "stats": st[0, 0].tolist(),
                       "alarm": {w: bool(first_alarm(st, w)[0] == 1) for w in WS}}
        print(title, "done", flush=True)
    chosen, worst, mean, where = choose(scen)
    res = {"seed": seed, "trials": trials, "series_trials": series_trials, "shares": WS, "verify": checks,
           "chosen": chosen, "in_use": drift.WINDOW_SHARE, "worst_regret": worst, "mean_regret": mean,
           "worst_where": where, "scenarios": scen, "false_alarms": null, "real": real}
    os.makedirs(OUT, exist_ok=True)
    with open(f"{OUT}/results.json", "w") as f:
        json.dump(res, f, indent=2, default=float)
    with open(f"{OUT}/report.md", "w") as f:
        f.write(render(res))
    return OUT


def render(res):
    ws = res["shares"]
    head = "| " + " | ".join(f"w = {w}" for w in ws) + " |"
    rule = "| " + " | ".join("---:" for _ in ws) + " |"
    row = lambda v: " | ".join(f"{v[w]:.1f}" for w in ws)
    L = ["# Which share of the error budget should the drift check's window test get?", "",
         "No API calls. Generated by `jevbench driftsplit` (#34) from the stored Jev answers of `jevbench driftvalidate`.",
         "", "The rows check of `pgdocket drift` alarms if either of two tests does: the recent window against the rows "
         "from before the series began, on a share *w* of the 5 % error budget spent over the checks, and evidence "
         "that never expires over every row since, on the rest. #33 used *w* = 0.5. Every share below saw the same "
         "draws.", "",
         "**The rule, fixed before the first run.** A share's regret in a scenario is the best share's detection "
         "rate there minus its own, in points. Choose the share whose largest regret is smallest; within 1 point of "
         "that, the one with the smaller mean regret. The scenarios are the series (at the first check after the "
         "change began, and by the end) and the first checks with injected shifts below. False alarms and the real "
         "shifts are reported, not used to choose.", "",
         f"**Chosen: w = {res['chosen']}** (in use: {res['in_use']}). Self-check: at w = 0.5 and at the share in use, "
         "the statistics agreed with `drift.rows_check` on "
         + " and ".join(f"{s} of {t} checks" for s, t, _ in res["verify"]) + ".", "",
         "| window share w | evidence share | largest regret | mean regret | where the largest is |",
         "| ---: | ---: | ---: | ---: | --- |"]
    for w in ws:
        mark = " (chosen)" if w == res["chosen"] else ""
        L.append(f"| {w}{mark} | {1 - w:.2f} | {res['worst_regret'][w]:.1f} | {res['mean_regret'][w]:.2f} | "
                 f"{res['worst_where'][w]} |")
    L += ["", f"## Detection rates (%), {res['series_trials']} runs per series and {res['trials']} per first check",
          "", "| scenario " + head, "| --- " + rule]
    L += [f"| {k} | {row(v)} |" for k, v in res["scenarios"].items()]
    L += ["", "## False alarms (%): first checks with no shift, and whole series with none", "",
          "| scenario " + head, "| --- " + rule]
    L += [f"| {k} | {row(v)} |" for k, v in res["false_alarms"].items()]
    L += ["", "## The real shifts by date, at a first check", "",
          "The most recent rows of each sample against everything before (as in `driftvalidate`).", "",
          "| task | rows: earlier → recent | p (answers) | p (scores) | " + " | ".join(f"w = {w}" for w in ws) + " |",
          "| --- | --- | ---: | ---: " + rule]
    for t, r in res["real"].items():
        p_mix, p_ks = r["stats"][:2]
        L.append(f"| {t} | {r['rows'][0]} → {r['rows'][1]} | {p_mix:.2g} | {p_ks:.2g} | "
                 + " | ".join("alarm" if r["alarm"][w] else "–" for w in ws) + " |")
    return "\n".join(L) + "\n"
