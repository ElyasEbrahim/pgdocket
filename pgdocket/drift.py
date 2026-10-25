"""Has the data or the model shifted since calibration? (issue #22)

Every guarantee pgdocket gives assumes that new rows look like the labelled ones and that the model answers the way
it did when it was calibrated. Two checks watch those assumptions:

- Data (free: no labels, no API calls). Recent rows against earlier ones, from the answers and scores already in the
  ledger: has any answer's share changed (a z-test per answer, Bonferroni over the answers), and have the scores moved
  (a two-sample Kolmogorov-Smirnov test). Two-sample tests on a classifier's own outputs are the strongest simple
  shift detectors (Rabanser et al., "Failing Loudly", NeurIPS 2019). With many rows tiny shifts become significant, so an alarm also needs a shift big enough to matter.
- Model (N API calls). N anchors, labelled rows, are asked again on the same input. Without drift, a row's old and new
  answers are two draws from the same model, so they are exchangeable: exact sign tests then check whether accuracy
  fell (one-sided, on the rows where exactly one answer is right) and whether scores moved (two-sided). This is the
  canary approach to silent API model changes, with labels added (cf. "Who Drifted", arXiv 2606.15474).

Checks are meant to run again and again, so the k-th check of a kind since the version was last calibrated spends
alpha * 6 / (pi^2 k^2) of the error budget: over the whole series the chance of any false alarm stays at most alpha.
Each check is stored in docket.drift_check.
"""
import json
from collections import Counter
from datetime import timedelta
from math import comb

import numpy as np
from psycopg import sql

from . import metrics as M
from .compare import sign_test

ALPHA = 0.05
MIN_ROWS = 30
MIN_SHIFT = 0.05       # an alarm on the answer mix needs an answer's share to move at least 5 points…
MIN_KS = 0.10          # …and on scores a Kolmogorov-Smirnov distance of at least 0.10


def sign_test_one_sided(worse, better):
    """P(at least `worse` of worse + better coin flips land 'worse'): exact, for an accuracy drop."""
    n = worse + better
    return 1.0 if n == 0 else float(sum(comb(n, i) for i in range(worse, n + 1)) / 2 ** n)


def windows(ref_answers, ref_scores, new_answers, new_scores, alpha_run):
    """Compare two windows of judged rows, each test on half the budget: has any answer's share changed (a z-test per
    answer, Bonferroni over the answers), and have the model's scores moved (two-sample KS on the score the
    thresholds use). Pure function: used by the CLI and by the validation harness."""
    ca, cb = Counter(ref_answers), Counter(new_answers)
    na, nb = len(ref_answers), len(new_answers)
    cats = set(ca) | set(cb)
    tv = 0.5 * sum(abs(cb.get(c, 0) / nb - ca.get(c, 0) / na) for c in cats)
    tests, k = M.proportion_tests(ca, cb)
    a2 = alpha_run / 2
    adj = [(c, x, y, min(1.0, p * k)) for c, x, y, p in tests]
    if k == 1:                                        # two answers, one test: report it as the share of yes
        adj = [t for t in adj if t[0] == "yes"] or adj[:1]
    hits = [t for t in adj if t[3] < a2 and abs(t[2] - t[1]) >= MIN_SHIFT]
    p_mix = min((t[3] for t in adj), default=1.0)
    d, p_score = M.ks_2samp(ref_scores, new_scores)
    return {"rows_ref": na, "rows_new": nb, "alpha": alpha_run,
            "mix": {"tv": tv, "p": p_mix, "alarm": bool(hits),
                    "moved": [{"answer": c, "before": x, "after": y, "p": p}
                              for c, x, y, p in sorted(hits or adj, key=lambda t: -abs(t[2] - t[1]))[:3]]},
            "scores": {"ks": d, "p": p_score, "alarm": bool(p_score < a2 and d >= MIN_KS),
                       "mean_before": float(np.mean(ref_scores)), "mean_after": float(np.mean(new_scores))}}


def anchors(orig_right, new_right, orig_scores, new_scores, alpha_run):
    """Compare old and new answers on the same labelled rows. Pure function, like `windows`."""
    o, n = np.asarray(orig_right, bool), np.asarray(new_right, bool)
    worse, better = int((o & ~n).sum()), int((~o & n).sum())
    diff = np.asarray(new_scores, float) - np.asarray(orig_scores, float)
    up, down = int((diff > 1e-9).sum()), int((diff < -1e-9).sum())
    p_acc, p_score = sign_test_one_sided(worse, better), sign_test(up, down)
    a2 = alpha_run / 2
    return {"anchors": len(o), "alpha": alpha_run,
            "accuracy": {"before": float(o.mean()), "after": float(n.mean()), "worse": worse, "better": better,
                         "p": p_acc, "alarm": bool(p_acc < a2)},
            "scores": {"up": up, "down": down, "mean_change": float(diff.mean()), "p": p_score, "alarm": bool(p_score < a2)}}


# ---------------------------------------------------------------- database
def _decision(conn, name):
    r = conn.execute("SELECT d.id, d.active_version, d.source, d.key_column, v.spec FROM docket.decision d "
                     "JOIN docket.version v ON v.decision_id = d.id AND v.version = d.active_version WHERE d.name = %s",
                     (name,)).fetchone()
    if not r:
        raise SystemExit(f"no decision named {name!r}")
    return dict(zip(("id", "version", "source", "key", "spec"), r))


def _answer(spec, value, score):
    return value if spec["kind"] == "choice" else ("yes" if score >= 0.5 else "no")


def next_run(conn, d, kind):
    """The k-th check of this kind since the active version was last calibrated, and its false-alarm budget.
    Recalibrating (the usual answer to an alarm) starts a new series."""
    k = conn.execute(
        "SELECT count(*) FROM docket.drift_check c JOIN docket.version v ON v.decision_id = c.decision_id "
        "AND v.version = c.version WHERE c.decision_id = %s AND c.version = %s AND c.kind = %s "
        "AND c.checked_at > coalesce((v.calibration->>'fitted_at')::timestamptz, '-infinity')",
        (d["id"], d["version"], kind)).fetchone()[0] + 1
    return k, M.spent_alpha(ALPHA, k)


def store(conn, d, kind, result):
    alarm = any(result[part]["alarm"] for part in result if isinstance(result[part], dict) and "alarm" in result[part])
    conn.execute("INSERT INTO docket.drift_check (decision_id, version, kind, alarm, result) VALUES (%s, %s, %s, %s, %s)",
                 (d["id"], d["version"], kind, alarm, json.dumps(result, default=float)))
    return alarm


def data_check(conn, name, days=7, by=None):
    """Rows of the active version from the last `days` days (counted back from the newest row) against earlier ones.
    `by`: a timestamp column of the source table; default: when the row was judged."""
    d = _decision(conn, name)
    when = (sql.SQL("s.{}").format(sql.Identifier(by)) if by else sql.SQL("j.judged_at"))
    rows = conn.execute(sql.SQL(
        "SELECT j.value, j.score, {w} FROM docket.current c JOIN docket.judgment j ON j.id = c.judgment_id "
        "JOIN {t} s ON s.{k}::text = c.row_key WHERE c.decision_id = %s AND c.version = %s AND {w} IS NOT NULL").format(
        w=when, t=sql.SQL(d["source"]), k=sql.Identifier(d["key"])), (d["id"], d["version"])).fetchall()
    if not rows:
        return d, {"note": "no judged rows yet"}
    cut = max(r[2] for r in rows) - timedelta(days=days)
    ref = [r for r in rows if r[2] < cut]
    new = [r for r in rows if r[2] >= cut]
    k, a = next_run(conn, d, "data")
    out = {"window_days": days, "by": by or "judged_at", "cut": cut.isoformat(), "run": k}
    if len(ref) < MIN_ROWS or len(new) < MIN_ROWS:
        hint = "" if by else "; to date rows by a column of the table instead of when they were judged, pass --by COLUMN"
        return d, out | {"note": f"{len(new):,} rows from the last {days:g} days and {len(ref):,} before them: the check "
                                 f"needs at least {MIN_ROWS} on each side{hint}"}
    out |= windows([_answer(d["spec"], v, s) for v, s, _ in ref], [s for _, s, _ in ref],
                   [_answer(d["spec"], v, s) for v, s, _ in new], [s for _, s, _ in new], a)
    out["alarm"] = store(conn, d, "data", out)
    return d, out


def model_check(conn, name, since=None):
    """Each anchor's current answer against its newest re-ask on the same input (made at or after `since`, when given)."""
    d = _decision(conn, name)
    rows = conn.execute("""
        SELECT l.label, jo.value, jo.score, pr.value, pr.score, extract(epoch FROM pr.judged_at - jo.judged_at) / 86400
        FROM docket.current c
        JOIN docket.judgment jo ON jo.id = c.judgment_id
        JOIN docket.label l ON l.decision_id = c.decision_id AND l.row_key = c.row_key AND l.sampling IN ('random', 'all')
        JOIN LATERAL (SELECT j.value, j.score, j.judged_at FROM docket.judgment j
                      WHERE j.decision_id = c.decision_id AND j.version = c.version AND j.row_key = c.row_key
                        AND j.purpose = 'probe' AND j.input_hash = jo.input_hash AND j.id > jo.id
                        AND j.judged_at >= coalesce(%(since)s::timestamptz, '-infinity')
                      ORDER BY j.id DESC LIMIT 1) pr ON true
        WHERE c.decision_id = %(d)s AND c.version = %(v)s""", {"since": since, "d": d["id"], "v": d["version"]}).fetchall()
    k, a = next_run(conn, d, "model")
    if len(rows) < 10:
        return d, {"run": k, "note": f"only {len(rows)} labelled rows have been asked again; queue anchors with "
                                     f"`pgdocket drift {name} --anchors 100 --run` (needs at least 10)"}
    spec = d["spec"]
    right = lambda label, v, s: _answer(spec, v, s) == label
    out = {"run": k, "days_apart": float(np.median([r[5] for r in rows]))} | anchors(
        [right(r[0], r[1], r[2]) for r in rows], [right(r[0], r[3], r[4]) for r in rows],
        [r[2] for r in rows], [r[4] for r in rows], a)
    out["alarm"] = store(conn, d, "model", out)
    return d, out


def pending(conn, d):
    """Re-asks of the active version still queued (not dead-lettered)."""
    return conn.execute("SELECT count(*) FROM docket.queue WHERE decision_id = %s AND version = %s AND purpose = 'probe' "
                        "AND attempts < 5", (d["id"], d["version"])).fetchone()[0]


def verdict_data(r):
    pct = lambda x: f"{100 * x:.1f} %"
    if "note" in r:
        return [f"data: {r['note']}"]
    head = (f"data: {r['rows_new']:,} rows from the last {r['window_days']} days (by {r['by']}) against {r['rows_ref']:,} "
            f"earlier · check {r['run']}, false-alarm budget {r['alpha']:.4f}")
    m, s = r["mix"], r["scores"]
    lines = [head,
             f"  answers: " + ("; ".join(f"{x['answer']} {pct(x['before'])} → {pct(x['after'])}" for x in m["moved"])
                               or "no answer common enough to test") + f" · p = {m['p']:.2g}"
             + ("  ⚠ shifted" if m["alarm"] else ""),
             f"  scores: KS distance {s['ks']:.3f}, mean {s['mean_before']:.3f} → {s['mean_after']:.3f}, p = {s['p']:.2g}"
             + ("  ⚠ shifted" if s["alarm"] else "")]
    lines.append("  → the recent rows don't look like the earlier ones: label a fresh random sample of recent rows, then "
                 "recalibrate and re-estimate" if r["alarm"] else "  → no shift detected")
    return lines


def verdict_model(r):
    if "note" in r:
        return [f"model: {r['note']}"]
    acc, s = r["accuracy"], r["scores"]
    pct = lambda x: f"{100 * x:.1f} %"
    lines = [f"model: {r['anchors']} labelled rows asked again, {r['days_apart']:.1f} days after their stored answer "
             f"(median) · check {r['run']}, false-alarm budget {r['alpha']:.4f}",
             f"  accuracy {pct(acc['before'])} → {pct(acc['after'])}: worse on {acc['worse']}, better on {acc['better']}, "
             f"p = {acc['p']:.2g}" + ("  ⚠ dropped" if acc["alarm"] else ""),
             f"  scores: up on {s['up']}, down on {s['down']}, mean change {s['mean_change']:+.3f}, p = {s['p']:.2g}"
             + ("  ⚠ shifted" if s["alarm"] else "")]
    if acc["alarm"]:
        lines.append("  → the model answers worse than when it was calibrated: compare a new version, or pin another model")
    elif s["alarm"]:
        lines.append("  → the model's scores moved: the calibration may be stale; recalibrate on the current answers")
    else:
        lines.append("  → no change in the model detected")
    return lines
