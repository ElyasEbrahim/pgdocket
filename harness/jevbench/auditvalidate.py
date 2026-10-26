"""Does `pgdocket audit` alarm when the accepted answers fall below target, and only then? (#23, no API calls)

- Simulation: false alarms when precision sits exactly at the target (the hardest case) and the test is read after
  every audited row; audited rows needed to catch a fall of 5-20 points.
- Real streams, every label known, auto-accepted rows audited in time order, 1,000 random audits each:
  - the live Kubernetes decision (`k8s_route` v1, 300 issues of March-June 2026, its stored calibration at 90 %),
    20 issues a month;
  - 2,000 CFPB complaints, calibrated on every labelled complaint of 2015-2021 (85 % target), then the 2022-2023
    complaints in date order, 20 per batch of 40 accepted complaints.
- Injected fall: the same CFPB stream, from batch 6 on a share of the right answers turned wrong, so the
  precision of the accepted rows drops to 80, 75 or 70 % (a silent model change). Labels from the change to the alarm.
"""
import json
import os
from collections import defaultdict

import numpy as np

from pgdocket.metrics import eprocess_below, precision_threshold

from .db import connect

OUT = "results/audit-validation"
BAD = 20                                          # an alarm: wealth >= 1 / alpha


def first_alarm(x, target):
    hit = np.flatnonzero(eprocess_below(x, target) >= BAD)
    return int(hit[0]) + 1 if len(hit) else None


def simulate(rng, trials=2000):
    out = {"false_alarms": {}, "detect": {}}
    for target in (0.9, 0.95):
        out["false_alarms"][target] = float(np.mean([first_alarm(rng.uniform(size=1000) < target, target) is not None
                                                     for _ in range(trials)]))
        for drop in (0.05, 0.10, 0.15, 0.20):
            first = [first_alarm(rng.uniform(size=3000) < target - drop, target) for _ in range(trials // 4)]
            first = np.array([np.inf if f is None else f for f in first])
            out["detect"][f"{target}/{drop}"] = {"median": float(np.median(first)), "p90": float(np.quantile(first, 0.9))}
    return out


def k8s_stream():
    """Accepted issues of the live decision, by month of creation: (month, right) in time order."""
    with connect() as conn:
        rows = conn.execute("""
            SELECT date_trunc('month', l.created_at)::date, j.value = l.sig, v.calibration->>'precision_target'
            FROM docket.v_k8s_route a
            JOIN demo.k8s_live l ON l.number = a.key
            JOIN docket.decision d ON d.name = 'k8s_route'
            JOIN docket.version v ON v.decision_id = d.id AND v.version = d.active_version
            JOIN docket.current c ON c.decision_id = d.id AND c.version = d.active_version AND c.row_key = a.key::text
            JOIN docket.judgment j ON j.id = c.judgment_id
            WHERE a.auto ORDER BY l.created_at""").fetchall()
    by = defaultdict(list)
    for month, right, _ in rows:
        by[str(month)].append(bool(right))
    return float(rows[0][2]), dict(by)


def cfpb_stream(target=0.85, per_batch=40):
    from .report import choice_probs, load_run
    with connect() as conn:
        _, exps, answers, truth = load_run(conn, "jev-20261017-122314")
        dates = dict(conn.execute("SELECT text_hash, date_received FROM bench.complaints").fetchall())
    opts = exps["choice_base"]["options"]
    rows = []
    for h, a in answers["choice_base"].items():
        if h in truth and dates.get(h):
            v = choice_probs(a, opts)[0]
            rows.append((dates[h], float(v.max()), opts[int(v.argmax())] == truth[h].product))
    rows.sort()
    year = np.array([r[0].year for r in rows])
    score = np.array([r[1] for r in rows])
    right = np.array([r[2] for r in rows])
    early = year <= 2021                                 # calibrated on every complaint of 2015-2021
    t = precision_threshold(score[early], right[early], target)["chosen"]["threshold"]
    later = [i for i in range(len(rows)) if year[i] >= 2022 and score[i] >= t]
    stream = {f"{i // per_batch + 1:02d}": [bool(right[j]) for j in later[i:i + per_batch]]
              for i in range(0, len(later), per_batch)}
    return t, stream


def audit(stream, target, per_batch, rng, trials=1000, inject=None):
    """Audit `per_batch` rows at random from each period, in time order. `inject`: (first batch, precision after)."""
    periods = sorted(stream)
    alarms, delays, false_before = 0, [], 0
    for _ in range(trials):
        x, change_at = [], None
        for b, p in enumerate(periods, start=1):
            pool = np.array(stream[p], float)
            if inject and b >= inject[0]:
                if change_at is None:
                    change_at = len(x)
                keep = inject[1] / pool.mean()               # turn right answers wrong until the period is at `after`
                pool = np.where(pool == 1, rng.uniform(size=len(pool)) < keep, 0.0)
            x += list(rng.choice(pool, min(per_batch, len(pool)), replace=False))
        f = first_alarm(np.array(x), target)
        alarms += f is not None
        if inject:
            if f is not None and f <= change_at:
                false_before += 1
            elif f is not None:
                delays.append(f - change_at)
    out = {"alarm_rate": alarms / trials, "audited": len(x)}
    if inject:
        out |= {"false_before_change": false_before / trials, "caught": len(delays) / trials, "after_change": len(x) - change_at,
                "median_labels_after_change": float(np.median(delays)) if delays else None}
    return out


def run(seed=0):
    rng = np.random.default_rng(seed)
    res = {"simulation": simulate(rng)}
    target, k8s = k8s_stream()
    res["k8s"] = {"target": target, "periods": {m: {"accepted": len(v), "precision": float(np.mean(v))} for m, v in k8s.items()},
                  "audit": audit(k8s, target, 20, rng)}
    t, cfpb = cfpb_stream()
    res["cfpb"] = {"target": 0.85, "threshold": t,
                   "periods": {b: {"accepted": len(v), "precision": float(np.mean(v))} for b, v in cfpb.items()},
                   "audit": audit(cfpb, 0.85, 20, rng),
                   "injected": {after: audit(cfpb, 0.85, 20, rng, trials=500, inject=(6, after)) for after in (0.80, 0.75, 0.70)}}
    os.makedirs(OUT, exist_ok=True)
    with open(f"{OUT}/results.json", "w") as f:
        json.dump(res, f, indent=2, default=float)
    with open(f"{OUT}/report.md", "w") as f:
        f.write(render(res))
    return OUT


def render(res):
    pct = lambda x: "–" if x is None else f"{100 * x:.1f} %"
    s = res["simulation"]
    L = ["# Does the audit alarm when the accepted answers fall below target, and only then?", "",
         "No API calls. Generated by `jevbench auditvalidate`. An alarm is the e-process reaching 20 (95 % confidence); "
         "the test is read after every audited row.", "", "## Simulation", "",
         "| target | false alarms over 1,000 audited rows at exactly the target | "
         + " | ".join(f"labels to catch a {int(100 * d)}-point fall (median / 90th pct)" for d in (0.05, 0.10, 0.15, 0.20)) + " |",
         "| ---: | ---: | " + " | ".join("---:" for _ in range(4)) + " |"]
    for target in (0.9, 0.95):
        cells = [s["detect"][f"{target}/{d}"] for d in (0.05, 0.10, 0.15, 0.20)]
        fmt = lambda c: f"{c['median']:.0f} / {c['p90']:.0f}" if np.isfinite(c["p90"]) else f"{c['median']:.0f} / >3,000"
        L.append(f"| {pct(target)} | {pct(s['false_alarms'][target])} | " + " | ".join(fmt(c) for c in cells) + " |")
    k = res["k8s"]
    L += ["", "## Real streams", "",
          f"**Kubernetes, the live decision** (target {pct(k['target'])}): 20 accepted issues audited a month, 1,000 random "
          "audits. Every issue is labelled, so the true precision of each month is known.", "",
          "| month | accepted issues | true precision |", "| --- | ---: | ---: |"]
    L += [f"| {m} | {v['accepted']} | {pct(v['precision'])} |" for m, v in k["periods"].items()]
    L += ["", f"Audits that alarmed: {pct(k['audit']['alarm_rate'])} ({k['audit']['audited']} issues audited).", ""]
    c = res["cfpb"]
    L += [f"**CFPB, 2022–2023** (calibrated on all 826 labelled complaints of 2015–2021 at 85 %: threshold {c['threshold']:.2f}): "
          "the accepted complaints in date order, in batches of 40, 20 audited from each.", "",
          "| batch | accepted complaints | true precision |", "| --- | ---: | ---: |"]
    L += [f"| {b} | {v['accepted']} | {pct(v['precision'])} |" for b, v in c["periods"].items()]
    L += ["", f"Audits that alarmed: {pct(c['audit']['alarm_rate'])} ({c['audit']['audited']} complaints audited).", "",
          "## Injected fall", "",
          "The same CFPB stream; from batch 6 on, right answers are turned wrong at random until the accepted rows are at "
          f"the stated precision. 500 audits each; {c['injected'][0.8]['after_change']} complaints are audited after the change.", "",
          "| precision after the change | alarms before the change | caught by the end | audited rows from the change to the alarm (median) |",
          "| ---: | ---: | ---: | ---: |"]
    for after, r in c["injected"].items():
        med = r["median_labels_after_change"]
        L.append(f"| {pct(float(after))} | {pct(r['false_before_change'])} | {pct(r['caught'])} | "
                 f"{'–' if med is None else f'{med:.0f}'} |")
    return "\n".join(L) + "\n"
