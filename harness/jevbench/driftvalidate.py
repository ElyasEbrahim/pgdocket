"""Does `pgdocket drift` alarm when it should, and only then? (#22, no API calls)

Data check, on three tasks judged by `pgdocket check` (500 rows each) and the Phase 0 product question (2,000
complaints), every label known:
- No shift: 1,000 random splits of the rows into a recent window and the rest. Rows in random order are
  exchangeable, so every alarm is false; at most 5 % are allowed.
- Injected shift: half the rows are the reference; the window is drawn from the other half with one true answer's
  share raised by delta, a label shift as in Rabanser et al. (2019). The detection rate shows what each window catches.
- By date: the most recent rows against everything before (issues by created_at, the last 90 days; complaints by
  date_received, the last 365).
- Series (#33): checks again and again, the reference and every check's new rows drawn from the same rows, with and
  without a lasting shift: the check as it was (#22: the window against everything before it, the whole budget
  spent over the checks) against the check as it is (the window against the rows from before the series began on
  `drift.WINDOW_SHARE` of the budget, 80 % since #34, and anytime-valid evidence over every row since on the rest).

Model check, on the 300 Kubernetes issues of `k8s_route` (every one labelled), with re-asks stored in the ledger:
- The same model asked again, twice, two days after its stored answers: every alarm is false.
- Real changes: jev-1.14 in place of jev-1.13 (what the jev-latest alias did), and the options' descriptions removed.
- Injected: the re-ask made wrong on a share of the anchors it got right; the detection rate by number of anchors.
Each as a first check, as it is (#35: sign tests on `drift.MODEL_SHARE` of the budget, and the evidence from the
panel's first re-asks on the rest) and as it was. Series of model checks: `jevbench driftmodel`.
"""
import json
import os
from collections import Counter
from datetime import timedelta

import numpy as np

from pgdocket import drift
from pgdocket.metrics import spent_alpha

from .db import connect

OUT = "results/drift-validation"
ALPHA = spent_alpha(0.05, 1)                    # the first check's budget as it was (#22); later checks spend less
# task: (stored answers, how rows are dated, recent-window sizes, days in the by-date window)
TASKS = {"Kubernetes: which SIG? (10 options)": ("check-k8s-sig", "issues", (50, 100), 90),
         "Kubernetes: is it a feature request? (yes/no)": ("check-k8s-feature", "issues", (50, 100), 90),
         "CFPB: which product? (9 options)": ("check-cfpb-product", "complaints", (50, 100), 365),
         "CFPB, 2,000 complaints: which product? (9 options)": ("phase0", "hashes", (100, 200, 400), 365)}
DELTAS = (0.0, 0.05, 0.10, 0.15, 0.20)
ANCHORS = (50, 100, 200)
DROPS = (0.02, 0.05, 0.10)
SCHEDULES = ((100, 12), (30, 52))               # rows per check, checks: weekly for a quarter, weekly for a year
SERIES = ((0.0, 1), (0.10, 1), (0.15, 1), (0.20, "mid"))   # shift, from which check


def load_check(folder):
    """(key, answer, the score the thresholds use, human label) per judged row."""
    out = []
    for line in open(f"results/{folder}/judgments.jsonl"):
        r = json.loads(line)
        a = r["answer"]
        if a["type"] == "noul":
            out.append((r["key"], "yes" if a["noul"] >= 0.5 else "no", float(a["noul"]), r["label"]))
        else:
            out.append((r["key"], a["choice"], float(a["probabilities"].get(a["choice"], a["confidence"])), r["label"]))
    return out


def load_phase0(run="jev-20261017-122314", experiment="choice_base"):
    """The Phase 0 product question on 2,000 complaints (20 rows per request), keyed by text hash."""
    from .report import choice_probs, load_run
    with connect() as conn:
        _, exps, answers, truth = load_run(conn, run)
    opts = exps[experiment]["options"]
    out = []
    for h, a in answers[experiment].items():
        if h in truth:
            v = choice_probs(a, opts)[0]
            out.append((h, opts[int(v.argmax())], float(v.max()), truth[h].product))
    return out


def any_alarm(r):
    return r["mix"]["alarm"] or r["scores"]["alarm"]


def split(rows, ref_idx, new_idx, old=False):
    """A first check: the window against the reference, as the check is (#33: `drift.rows_check`) or as it was."""
    pick = lambda idx, j: [rows[i][j] for i in idx]
    if old:
        return drift.windows(pick(ref_idx, 1), pick(ref_idx, 2), pick(new_idx, 1), pick(new_idx, 2), ALPHA)
    n = len(new_idx)
    return drift.rows_check(pick(ref_idx, 1), pick(ref_idx, 2), pick(new_idx, 1), pick(new_idx, 2), [1] * n, [True] * n, 1)


def no_shift(rows, m, trials, rng):
    hits, old = 0, 0
    for _ in range(trials):
        idx = rng.permutation(len(rows))
        hits += any_alarm(split(rows, idx[m:], idx[:m]))
        old += any_alarm(split(rows, idx[m:], idx[:m], old=True))
    return {"now": hits / trials, "before": old / trials}


def injected(rows, m, delta, trials, rng):
    """Window of m rows whose true-answer mix has one answer (the second most common) delta more often."""
    labels = np.array([r[3] for r in rows])
    target = Counter(labels).most_common(2)[1][0]
    hits = done = old = 0
    for _ in range(trials):
        idx = rng.permutation(len(rows))
        ref, pool = idx[: len(rows) // 2], idx[len(rows) // 2:]
        k = round(m * ((labels[ref] == target).mean() + delta))
        yes, no = pool[labels[pool] == target], pool[labels[pool] != target]
        if k > len(yes) or m - k > len(no):
            continue
        new = np.r_[rng.choice(yes, k, replace=False), rng.choice(no, m - k, replace=False)]
        hits += any_alarm(split(rows, ref, new))
        old += any_alarm(split(rows, ref, new, old=True))
        done += 1
    return {"target": str(target), "detected": hits / done if done else None,
            "detected_before": old / done if done else None, "trials": done}


def by_date(rows, dates, days=90):
    keep = [i for i, r in enumerate(rows) if dates.get(r[0]) is not None]
    cut = max(dates[rows[i][0]] for i in keep) - timedelta(days=days)
    new = [i for i in keep if dates[rows[i][0]] >= cut]
    ref = [i for i in keep if dates[rows[i][0]] < cut]
    r = split(rows, ref, new)
    r["alarm_before"] = any_alarm(split(rows, ref, new, old=True))
    ta, tb = Counter(rows[i][3] for i in ref), Counter(rows[i][3] for i in new)
    r["true_tv"] = 0.5 * sum(abs(tb.get(c, 0) / len(new) - ta.get(c, 0) / len(ref)) for c in set(ta) | set(tb))
    r["cut"] = cut.isoformat()
    return r


def dates_for(kind, keys):
    q = {"issues": "SELECT number::text, created_at FROM demo.k8s_issues WHERE number::text = ANY(%s)",
         "complaints": "SELECT complaint_id, date_received FROM bench.complaints WHERE complaint_id = ANY(%s)",
         "hashes": "SELECT text_hash, date_received FROM bench.complaints WHERE text_hash = ANY(%s)"}[kind]
    with connect() as conn:
        return dict(conn.execute(q, (keys,)).fetchall())


def _first_old(R, batches):
    """The first check at which the check as it was (#22) alarms: each window against everything before it."""
    before = list(R)
    for j, win in enumerate(batches, 1):
        r = drift.windows([x[0] for x in before], [x[1] for x in before], [x[0] for x in win], [x[1] for x in win],
                          spent_alpha(0.05, j))
        if any_alarm(r):
            return j
        before += win
    return None


def _first_now(R, batches):
    """The first check at which the check as it is alarms (`drift.rows_check`): blocks of one window each."""
    new, day = [], []
    for j, win in enumerate(batches, 1):
        new += win
        day += [j] * len(win)
        recent = [d == j for d in day]
        r = drift.rows_check([x[0] for x in R], [x[1] for x in R], [x[0] for x in new], [x[1] for x in new], day,
                             recent, j)
        if any_alarm(r):
            return j
    return None


def series(rows, b, checks, delta, start, trials, rng, m=1000):
    """Checks again and again: a reference of m rows and every check's b new rows drawn from the task's rows, from
    check `start` on with one true answer (the second most common) delta more often, the others in proportion. The
    share of runs that alarmed by each check, for the check as it was and as it is."""
    labels = np.array([r[3] for r in rows])
    target = Counter(labels).most_common(2)[1][0]
    yes, no = np.flatnonzero(labels == target), np.flatnonzero(labels != target)
    base = len(yes) / len(rows)
    start = checks // 2 + 1 if start == "mid" else start
    first = {"before": [], "now": []}
    for _ in range(trials):
        R = [(rows[i][1], rows[i][2]) for i in rng.integers(0, len(rows), m)]
        batches = []
        for j in range(1, checks + 1):
            k = rng.binomial(b, min(1.0, base + (delta if j >= start else 0.0)))
            pick = np.r_[rng.choice(yes, k), rng.choice(no, b - k)]
            rng.shuffle(pick)                                  # a check's rows come in no particular order
            batches.append([(rows[i][1], rows[i][2]) for i in pick])
        first["before"].append(_first_old(R, batches))
        first["now"].append(_first_now(R, batches))
    marks = sorted({1, max(1, checks // 4), checks // 2, checks // 2 + 1, checks})   # the same for every shift
    by = lambda f, j: float(np.mean([x is not None and x <= j for x in f]))
    return {"target": str(target), "start": start, "marks": marks,
            **{k: {j: by(f, j) for j in marks} for k, f in first.items()}}


# ---------------------------------------------------------------- model check
def load_anchors(name="k8s_route"):
    """Per row: label, v1's stored answer, its two re-asks (oldest first), v2's and v3's answers, each (right, score)."""
    with connect() as conn:
        rows = conn.execute("""
            SELECT c.row_key, l.sig, jo.value, jo.score,
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
    lab = [r[1] for r in rows]
    right = lambda vals: np.array([v == l for v, l in zip(vals, lab)])
    orig = (right([r[2] for r in rows]), np.array([r[3] for r in rows]))
    return orig, {
        "the same model asked again (first re-ask)": (right([r[4][0] for r in rows]), np.array([r[5][0] for r in rows])),
        "the same model asked again (second re-ask)": (right([r[4][1] for r in rows]), np.array([r[5][1] for r in rows])),
        "jev-1.14 in place of jev-1.13": (right([r[8] for r in rows]), np.array([r[9] for r in rows])),
        "options without their descriptions": (right([r[6] for r in rows]), np.array([r[7] for r in rows])),
    }


def first_model(o_right, n_right, o_scores, n_scores):
    """A first model check as it is (#35): the sign tests on MODEL_SHARE of the budget, and the evidence from these
    rows, each asked for the first time in the series."""
    return drift.model_rows(o_right, n_right, o_scores, n_scores, 1, drift.signs(o_right, n_right, o_scores, n_scores))


def model_rates(orig, new, trials, rng, drop=0.0):
    out = {}
    for n in ANCHORS:
        now = {"acc": 0, "score": 0, "any": 0}
        was = {"acc": 0, "score": 0, "any": 0}
        for _ in range(trials):
            idx = rng.choice(len(orig[0]), n, replace=False)
            nr = new[0][idx].copy()
            if drop:
                ok = np.flatnonzero(nr)
                nr[rng.choice(ok, min(len(ok), round(drop * n)), replace=False)] = False
            for tally, r in ((now, first_model(orig[0][idx], nr, orig[1][idx], new[1][idx])),
                             (was, drift.anchors(orig[0][idx], nr, orig[1][idx], new[1][idx], ALPHA))):
                tally["acc"] += r["accuracy"]["alarm"]
                tally["score"] += r["scores"]["alarm"]
                tally["any"] += r["accuracy"]["alarm"] or r["scores"]["alarm"]
        out[n] = {"accuracy_alarm": now["acc"] / trials, "score_alarm": now["score"] / trials,
                  "any_alarm": now["any"] / trials, "accuracy_alarm_before": was["acc"] / trials,
                  "any_alarm_before": was["any"] / trials}
    return out


def run(trials=1000, seed=0, series_trials=200):
    rng = np.random.default_rng(seed)
    res = {"alpha": ALPHA, "data": {}, "model": {}, "series_trials": series_trials}
    for title, (source, kind, sizes, days) in TASKS.items():
        rows = load_phase0() if source == "phase0" else load_check(source)
        res["data"][title] = {
            "rows": len(rows), "days": days,
            "no_shift": {m: no_shift(rows, m, trials, rng) for m in sizes},
            "injected": {m: {d: injected(rows, m, d, trials // 2, rng) for d in DELTAS} for m in sizes},
            "by_date": by_date(rows, dates_for(kind, [r[0] for r in rows]), days),
            "series": {f"{b} rows a check, {c} checks": {f"{d}@{st}": series(rows, b, c, d, st, series_trials, rng)
                                                         for d, st in SERIES} for b, c in SCHEDULES},
        }
        print(title, "done", flush=True)
    orig, changes = load_anchors()
    for title, new in changes.items():
        full = first_model(orig[0], new[0], orig[1], new[1])
        was = drift.anchors(orig[0], new[0], orig[1], new[1], ALPHA)
        full["alarm_before"] = bool(was["accuracy"]["alarm"] or was["scores"]["alarm"])
        res["model"][title] = {"all_300": full, "subsets": model_rates(orig, new, trials, rng)}
    base = changes["the same model asked again (first re-ask)"]
    res["model_injected"] = {d: model_rates(orig, base, trials // 2, rng, drop=d) for d in DROPS}
    os.makedirs(OUT, exist_ok=True)
    with open(f"{OUT}/results.json", "w") as f:
        json.dump(res, f, indent=2, default=float)
    with open(f"{OUT}/report.md", "w") as f:
        f.write(render(res))
    return OUT


def render(res):
    pct = lambda x: "–" if x is None else f"{100 * x:.1f} %"
    L = ["# Does the drift check alarm when it should, and only then?", "",
         "No API calls. Generated by `jevbench driftvalidate` from stored Jev answers. The rows check is shown as it is "
         f"(#33, #34: the recent window against the rows from before the series began, on {100 * drift.WINDOW_SHARE:.0f} % "
         "of the error budget spent over the checks, and anytime-valid evidence over every row since, on the other "
         f"{100 - 100 * drift.WINDOW_SHARE:.0f} %) and as it was (#22: the "
         f"window against everything before it, the whole budget spent over the checks; {ALPHA:.4f} at the first).", "",
         "## Data check (free)", "",
         "**No shift.** 1,000 random splits of each task's judged rows into a recent window and the rest, as a first "
         "check. Every alarm is false (at most 5 % allowed).", "",
         "| task | rows | recent window | false alarms | as it was |", "| --- | ---: | ---: | ---: | ---: |"]
    for t, d in res["data"].items():
        for m, rate in d["no_shift"].items():
            L.append(f"| {t} | {d['rows']:,} | {m} rows | {pct(rate['now'])} | {pct(rate['before'])} |")
    L += ["", "**Injected shift.** Half the rows are the reference; the window is drawn from the other half with one "
          "true answer (the second most common) δ more frequent. Share of 500 first checks with an alarm, as it is "
          "(as it was):", "",
          "| task | answer made more frequent | window | " + " | ".join(f"δ = {100 * d:.0f} pp" for d in DELTAS) + " |",
          "| --- | --- | ---: | " + " | ".join("---:" for _ in DELTAS) + " |"]
    for t, d in res["data"].items():
        for m, cells in d["injected"].items():
            L.append(f"| {t} | {cells[DELTAS[0]]['target'].split(':')[0]} | {m} | "
                     + " | ".join(f"{pct(cells[x]['detected'])} ({pct(cells[x]['detected_before'])})" for x in DELTAS)
                     + " |")
    L += ["", f"**Series.** Checks again and again, {res.get('series_trials', '')} runs per row: a reference of 1,000 "
          "rows and every check's new rows drawn from the task's rows, with one true answer (the second most common) "
          "made δ more frequent from a given check on. Share of runs with an alarm by each check, as it is (as it was):",
          ""]
    for t, d in res["data"].items():
        for sched, cells in d.get("series", {}).items():
            L += [f"*{t}, {sched}:*", "", "| shift | " + " | ".join(f"by check {j}" for j in next(iter(cells.values()))["marks"])
                  + " |", "| --- | " + " | ".join("---:" for _ in next(iter(cells.values()))["marks"]) + " |"]
            for key, c in cells.items():
                d_, _ = key.split("@")
                what = "none" if float(d_) == 0 else f"δ = {100 * float(d_):.0f} pp from check {c['start']}"
                L.append(f"| {what} | " + " | ".join(f"{pct(c['now'][j])} ({pct(c['before'][j])})" for j in c["marks"])
                         + " |")
            L.append("")
    L += ["", "**By date.** The most recent rows of each sample (the last 90 days of issues, the last 365 days of "
          "complaints) against everything before. *True shift* is the total variation between the two windows' human "
          "labels, which the check never sees.", "",
          "| task | rows: earlier → recent | answer that moved most (p) | scores: KS (p) | true shift | alarm (as it was) |",
          "| --- | --- | --- | --- | ---: | --- |"]
    for t, d in res["data"].items():
        b = d["by_date"]
        mv = b["mix"]["moved"][0]
        L.append(f"| {t} | {b['rows_ref']} → {b['rows_new']} | {mv['answer'].split(':')[0]} {pct(mv['before'])} → "
                 f"{pct(mv['after'])} ({b['mix']['p']:.2g}) | {b['scores']['ks']:.3f} ({b['scores']['p']:.2g}) | {pct(b['true_tv'])} | "
                 f"{'yes' if any_alarm(b) else 'no'} ({'yes' if b['alarm_before'] else 'no'}) |")
    L += ["", "## Model check (N API calls)", "",
          "The 300 Kubernetes issues of `k8s_route`, each with a human label. The stored answers (jev-1.13, 20 Oct) "
          "against what came back when the same rows were asked again (22 Oct), and against two real changes. "
          "*Worse/better*: rows where only the stored answer / only the new one is right. A first check, as it is "
          f"(#35: sign tests on {100 * drift.MODEL_SHARE:.0f} % of the budget, and the evidence from these rows' first "
          "re-asks on the rest) and as it was (#22: sign tests on the whole first-check budget).", "",
          "| new answers | accuracy | worse / better | p (accuracy) | scores up / down | p (scores) | alarm on 300 | "
          + " | ".join(f"alarm rate, {n} anchors" for n in ANCHORS) + " |",
          "| --- | --- | --- | --- | --- | --- | --- | " + " | ".join("---:" for _ in ANCHORS) + " |"]
    for t, m in res["model"].items():
        a = m["all_300"]
        alarm = ("accuracy dropped" if a["accuracy"]["alarm"] else "") or ("scores moved" if a["scores"]["alarm"] else "no")
        L.append(f"| {t} | {pct(a['accuracy']['before'])} → {pct(a['accuracy']['after'])} | {a['accuracy']['worse']} / "
                 f"{a['accuracy']['better']} | {a['accuracy']['p']:.2g} | {a['scores']['up']} / {a['scores']['down']} | "
                 f"{a['scores']['p']:.2g} | {alarm} ({'yes' if a.get('alarm_before') else 'no'}) | "
                 + " | ".join(f"{pct(m['subsets'][n]['any_alarm'])} ({pct(m['subsets'][n]['any_alarm_before'])})"
                              for n in ANCHORS) + " |")
    L += ["", "*Alarm rate* is the share of 1,000 random anchor panels of that size on which the check alarms, as it is "
          "(as it was).", "",
          "**Injected drop.** The first re-ask, made wrong on a share of the anchors it got right. Share of 500 panels "
          "on which the accuracy test alarms at a first check, as it is (as it was):", "",
          "| accuracy drop | " + " | ".join(f"{n} anchors" for n in ANCHORS) + " |",
          "| ---: | " + " | ".join("---:" for _ in ANCHORS) + " |"]
    for dr, rates in res["model_injected"].items():
        L.append(f"| {100 * float(dr):.0f} pp | " + " | ".join(
            f"{pct(rates[n]['accuracy_alarm'])} ({pct(rates[n]['accuracy_alarm_before'])})" for n in ANCHORS) + " |")
    return "\n".join(L) + "\n"
