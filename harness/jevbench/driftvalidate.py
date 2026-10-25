"""Does `pgdocket drift` alarm when it should, and only then? (#22, no API calls)

Data check, on three tasks judged by `pgdocket check` (500 rows each) and the Phase 0 product question (2,000
complaints), every label known:
- No shift: 1,000 random splits of the rows into a recent window and the rest. Rows in random order are
  exchangeable, so every alarm is false; at most 5 % are allowed.
- Injected shift: half the rows are the reference; the window is drawn from the other half with one true answer's
  share raised by delta, a label shift as in Rabanser et al. (2019). The detection rate shows what each window catches.
- By date: the most recent rows against everything before (issues by created_at, the last 90 days; complaints by
  date_received, the last 365).

Model check, on the 300 Kubernetes issues of `k8s_route` (every one labelled), with re-asks stored in the ledger:
- The same model asked again, twice, two days after its stored answers: every alarm is false.
- Real changes: jev-1.14 in place of jev-1.13 (what the jev-latest alias did), and the options' descriptions removed.
- Injected: the re-ask made wrong on a share of the anchors it got right; the detection rate by number of anchors.
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
ALPHA = spent_alpha(0.05, 1)                    # the first check's budget; later checks spend less
# task: (stored answers, how rows are dated, recent-window sizes, days in the by-date window)
TASKS = {"Kubernetes: which SIG? (10 options)": ("check-k8s-sig", "issues", (50, 100), 90),
         "Kubernetes: is it a feature request? (yes/no)": ("check-k8s-feature", "issues", (50, 100), 90),
         "CFPB: which product? (9 options)": ("check-cfpb-product", "complaints", (50, 100), 365),
         "CFPB, 2,000 complaints: which product? (9 options)": ("phase0", "hashes", (100, 200, 400), 365)}
DELTAS = (0.0, 0.05, 0.10, 0.15, 0.20)
ANCHORS = (50, 100, 200)
DROPS = (0.02, 0.05, 0.10)


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


def split(rows, ref_idx, new_idx):
    pick = lambda idx, j: [rows[i][j] for i in idx]
    return drift.windows(pick(ref_idx, 1), pick(ref_idx, 2), pick(new_idx, 1), pick(new_idx, 2), ALPHA)


def no_shift(rows, m, trials, rng):
    hits = 0
    for _ in range(trials):
        idx = rng.permutation(len(rows))
        hits += any_alarm(split(rows, idx[m:], idx[:m]))
    return hits / trials


def injected(rows, m, delta, trials, rng):
    """Window of m rows whose true-answer mix has one answer (the second most common) delta more often."""
    labels = np.array([r[3] for r in rows])
    target = Counter(labels).most_common(2)[1][0]
    hits = done = 0
    for _ in range(trials):
        idx = rng.permutation(len(rows))
        ref, pool = idx[: len(rows) // 2], idx[len(rows) // 2:]
        k = round(m * ((labels[ref] == target).mean() + delta))
        yes, no = pool[labels[pool] == target], pool[labels[pool] != target]
        if k > len(yes) or m - k > len(no):
            continue
        new = np.r_[rng.choice(yes, k, replace=False), rng.choice(no, m - k, replace=False)]
        hits += any_alarm(split(rows, ref, new))
        done += 1
    return {"target": str(target), "detected": hits / done if done else None, "trials": done}


def by_date(rows, dates, days=90):
    keep = [i for i, r in enumerate(rows) if dates.get(r[0]) is not None]
    cut = max(dates[rows[i][0]] for i in keep) - timedelta(days=days)
    new = [i for i in keep if dates[rows[i][0]] >= cut]
    ref = [i for i in keep if dates[rows[i][0]] < cut]
    r = split(rows, ref, new)
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


def model_rates(orig, new, trials, rng, drop=0.0):
    out = {}
    for n in ANCHORS:
        acc = score = either = 0
        for _ in range(trials):
            idx = rng.choice(len(orig[0]), n, replace=False)
            nr = new[0][idx].copy()
            if drop:
                ok = np.flatnonzero(nr)
                nr[rng.choice(ok, min(len(ok), round(drop * n)), replace=False)] = False
            r = drift.anchors(orig[0][idx], nr, orig[1][idx], new[1][idx], ALPHA)
            acc += r["accuracy"]["alarm"]
            score += r["scores"]["alarm"]
            either += r["accuracy"]["alarm"] or r["scores"]["alarm"]
        out[n] = {"accuracy_alarm": acc / trials, "score_alarm": score / trials, "any_alarm": either / trials}
    return out


def run(trials=1000, seed=0):
    rng = np.random.default_rng(seed)
    res = {"alpha": ALPHA, "data": {}, "model": {}}
    for title, (source, kind, sizes, days) in TASKS.items():
        rows = load_phase0() if source == "phase0" else load_check(source)
        res["data"][title] = {
            "rows": len(rows), "days": days,
            "no_shift": {m: no_shift(rows, m, trials, rng) for m in sizes},
            "injected": {m: {d: injected(rows, m, d, trials // 2, rng) for d in DELTAS} for m in sizes},
            "by_date": by_date(rows, dates_for(kind, [r[0] for r in rows]), days),
        }
    orig, changes = load_anchors()
    for title, new in changes.items():
        full = drift.anchors(orig[0], new[0], orig[1], new[1], ALPHA)
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
         "No API calls. Generated by `jevbench driftvalidate` from stored Jev answers. Every check uses the first run's "
         f"false-alarm budget ({ALPHA:.4f}, split between the two tests of each check); later runs spend less, so they "
         "alarm less often.", "", "## Data check (free)", "",
         "**No shift.** 1,000 random splits of each task's judged rows into a recent window and the rest. Every "
         "alarm is false (at most 5 % allowed).", "",
         "| task | rows | recent window | false alarms |", "| --- | ---: | ---: | ---: |"]
    for t, d in res["data"].items():
        for m, rate in d["no_shift"].items():
            L.append(f"| {t} | {d['rows']:,} | {m} rows | {pct(rate)} |")
    L += ["", "**Injected shift.** Half the rows are the reference; the window is drawn from the other half with one "
          "true answer (the second most common) δ more frequent. Share of 500 trials with an alarm:", "",
          "| task | answer made more frequent | window | " + " | ".join(f"δ = {100 * d:.0f} pp" for d in DELTAS) + " |",
          "| --- | --- | ---: | " + " | ".join("---:" for _ in DELTAS) + " |"]
    for t, d in res["data"].items():
        for m, cells in d["injected"].items():
            L.append(f"| {t} | {cells[DELTAS[0]]['target'].split(':')[0]} | {m} | "
                     + " | ".join(pct(cells[x]["detected"]) for x in DELTAS) + " |")
    L += ["", "**By date.** The most recent rows of each sample (the last 90 days of issues, the last 365 days of "
          "complaints) against everything before. *True shift* is the total variation between the two windows' human "
          "labels, which the check never sees.", "",
          "| task | rows: earlier → recent | answer that moved most (p) | scores: KS (p) | true shift | alarm |",
          "| --- | --- | --- | --- | ---: | --- |"]
    for t, d in res["data"].items():
        b = d["by_date"]
        mv = b["mix"]["moved"][0]
        L.append(f"| {t} | {b['rows_ref']} → {b['rows_new']} | {mv['answer'].split(':')[0]} {pct(mv['before'])} → "
                 f"{pct(mv['after'])} ({b['mix']['p']:.2g}) | {b['scores']['ks']:.3f} ({b['scores']['p']:.2g}) | {pct(b['true_tv'])} | "
                 f"{'yes' if any_alarm(b) else 'no'} |")
    L += ["", "## Model check (N API calls)", "",
          "The 300 Kubernetes issues of `k8s_route`, each with a human label. The stored answers (jev-1.13, 20 Oct) "
          "against what came back when the same rows were asked again (22 Oct), and against two real changes. "
          "*Worse/better*: rows where only the stored answer / only the new one is right.", "",
          "| new answers | accuracy | worse / better | p (accuracy) | scores up / down | p (scores) | alarm on 300 | "
          + " | ".join(f"alarm rate, {n} anchors" for n in ANCHORS) + " |",
          "| --- | --- | --- | --- | --- | --- | --- | " + " | ".join("---:" for _ in ANCHORS) + " |"]
    for t, m in res["model"].items():
        a = m["all_300"]
        alarm = ("accuracy dropped" if a["accuracy"]["alarm"] else "") or ("scores moved" if a["scores"]["alarm"] else "no")
        L.append(f"| {t} | {pct(a['accuracy']['before'])} → {pct(a['accuracy']['after'])} | {a['accuracy']['worse']} / "
                 f"{a['accuracy']['better']} | {a['accuracy']['p']:.2g} | {a['scores']['up']} / {a['scores']['down']} | "
                 f"{a['scores']['p']:.2g} | {alarm} | "
                 + " | ".join(pct(m["subsets"][n]["any_alarm"]) for n in ANCHORS) + " |")
    L += ["", "*Alarm rate* is the share of 1,000 random anchor panels of that size on which either test alarms.", "",
          "**Injected drop.** The first re-ask, made wrong on a share of the anchors it got right. Share of 500 panels "
          "on which the accuracy test alarms:", "",
          "| accuracy drop | " + " | ".join(f"{n} anchors" for n in ANCHORS) + " |",
          "| ---: | " + " | ".join("---:" for _ in ANCHORS) + " |"]
    for dr, rates in res["model_injected"].items():
        L.append(f"| {100 * float(dr):.0f} pp | " + " | ".join(pct(rates[n]["accuracy_alarm"]) for n in ANCHORS) + " |")
    return "\n".join(L) + "\n"
