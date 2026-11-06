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

**Anytime-valid rows (#33).** A budget that shrinks with every check starves frequent checks: the 30th gets 0.00017.
The rows check now runs two tests on half the budget each, and alarms if either does:
- the window test above, the recent window against the rows from before the series began (its first check's cut), not
  against everything before the window: once a shift has lasted, the shifted rows would otherwise become the reference,
  and the alarms stop;
- evidence that never expires: one e-process per answer, and per decile of the reference's scores, over every row since
  that cut (`log_evidence`). Its chance of ever reaching 1/alpha is at most alpha however often it is read, and a
  shift that lasts piles it up instead of being tested afresh against a smaller budget each time.
The model check keeps its budget: every check compares re-asks with the same stored answers, whose labels say whether
they were right, so one check's outcome tells about the next and their e-values could not be multiplied.
"""
import json
import math
from collections import Counter
from datetime import date, datetime, timedelta
from math import comb

import numpy as np
from psycopg import sql

from . import metrics as M
from .compare import sign_test

ALPHA = 0.05
LOG_PI = math.log(math.pi)
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


def _log_kt(k, n):
    """Krichevsky-Trofimov (Jeffreys mixture) log-probability of a 0/1 sequence with k ones among n."""
    return math.lgamma(k + 0.5) + math.lgamma(n - k + 0.5) - math.lgamma(n + 1) - LOG_PI


def _log_ml(k, n):
    """Its maximum likelihood: k ones among n at the share k / n."""
    return (k * math.log(k / n) if k else 0.0) + ((n - k) * math.log((n - k) / n) if k < n else 0.0)


def log_evidence(k_ref, m, ks, ns):
    """log e-value that one 0/1 feature of the rows (an answer, or a score at most some value) changed after the
    reference: k_ref of the m reference rows have it, and ks[b] of the ns[b] rows of block b since the cut (b = 1, 2,
    …: the check window's length, so that a change can begin with any window).

    Universal inference (Wasserman, Ramdas & Balakrishnan, 2020): without a change, every row is one draw from the same
    Bernoulli distribution, whose likelihood the maximum over all of them can only exceed. Against it, a change that
    began with block b, b = 1, 2, … with prior 1 / (b (b + 1)), the share before and after it each a
    Krichevsky-Trofimov mixture; blocks to come weigh what the prior has left. That mixture is one probability over the whole stream, so its
    ratio to the true likelihood is a test martingale, and this ratio, to the maximum, is an e-process: by Ville's
    inequality, the chance it ever reaches 1/alpha is at most alpha, however often it is read."""
    ks, ns = [int(x) for x in ks], [int(x) for x in ns]
    K, N = k_ref + sum(ks), m + sum(ns)
    terms, k_pre, n_pre, k_post, n_post = [], k_ref, m, sum(ks), sum(ns)
    for d in range(1, len(ks) + 1):                       # the change began with block d
        terms.append(-math.log(d * (d + 1)) + _log_kt(k_pre, n_pre) + _log_kt(k_post, n_post))
        k_pre, n_pre, k_post, n_post = k_pre + ks[d - 1], n_pre + ns[d - 1], k_post - ks[d - 1], n_post - ns[d - 1]
    terms.append(-math.log(len(ks) + 1) + _log_kt(K, N))  # it hasn't begun yet
    top = max(terms)
    return top + math.log(sum(math.exp(t - top) for t in terms)) - _log_ml(K, N)


def evidence(ref, new, day, recent, kind, alpha, floor):
    """Anytime-valid evidence that the rows since the cut (`new`, in block `day` 1, 2, … since it) differ from the
    reference rows (`ref`): an e-process (`log_evidence`) per answer (kind 'answers'; one for two answers) or per decile of the
    reference's scores ('scores': the share scored at most it), Bonferroni over them at `alpha`. An alarm also needs the
    recent window (`recent`, a mask over the new rows) to have moved that share by `floor`, as the window test does."""
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
    thr = math.log(len(items) / alpha)
    D = int(day.max()) if len(day) else 0
    best = None
    for c in items:
        x = has(new, c).astype(float)
        k_ref = int(has(ref, c).sum())
        e = log_evidence(k_ref, len(ref), np.bincount(day, weights=x, minlength=D + 1)[1:],
                         np.bincount(day, minlength=D + 1)[1:])
        before, after = k_ref / len(ref), (float(x[recent].mean()) if recent.any() else None)
        alarm = bool(e >= thr and after is not None and abs(after - before) >= floor)
        if best is None or (alarm, e) > best[0]:
            best = ((alarm, e), {"item": c, "before": before, "after": after, "log_e": e, "alarm": alarm})
    return {"threshold": thr, "alpha": alpha, "tests": len(items), **best[1]}


def rows_check(ref_answers, ref_scores, new_answers, new_scores, day, recent, run):
    """The rows check (#33): the recent window (`recent`, a mask over the rows since the cut) against the reference
    (`windows`, on half the error budget, spent over the series' checks), and the evidence over every row since the cut
    (`evidence`, on the other half: a quarter on the answers, a quarter on the scores). Either can raise the alarm."""
    rec = np.asarray(recent, bool)
    pick = lambda v: [x for x, r in zip(v, rec) if r]
    out = windows(ref_answers, ref_scores, pick(new_answers), pick(new_scores), M.spent_alpha(ALPHA / 2, run))
    for part, values, ref, kind, floor in (("mix", new_answers, ref_answers, "answers", MIN_SHIFT),
                                           ("scores", new_scores, ref_scores, "scores", MIN_KS)):
        ev = evidence(ref, values, day, rec, kind, ALPHA / 4, floor)
        out[part]["evidence"] = ev
        out[part]["alarm"] = bool(out[part]["alarm"] or ev["alarm"])
    return out


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


def _series(conn, d, by):
    """This rows check's number in its series (the checks since the active version was last calibrated), and its first
    check dated the same way: where it divided the rows (the cut) and its window's length, the evidence's blocks (None
    before that check)."""
    done = conn.execute(
        "SELECT c.result FROM docket.drift_check c JOIN docket.version v ON v.decision_id = c.decision_id "
        "AND v.version = c.version WHERE c.decision_id = %s AND c.version = %s AND c.kind = 'data' "
        "AND c.checked_at > coalesce((v.calibration->>'fitted_at')::timestamptz, '-infinity') ORDER BY c.id",
        (d["id"], d["version"])).fetchall()
    first = next((r for (r,) in done if r.get("by", "judged_at") == by and "cut" in r and "note" not in r), None)
    return len(done) + 1, first


def _like(text, sample):
    """A stored cut, as the kind of value the rows are dated with (a date or a timestamp)."""
    t = datetime.fromisoformat(text)
    return t.date() if isinstance(sample, date) and not isinstance(sample, datetime) else t


def data_check(conn, name, days=7, by=None):
    """Rows of the active version from the last `days` days (counted back from the newest row) against the rows from
    before the series began, and the evidence over every row since (`rows_check`). `by`: a timestamp column of the
    source table; default: when the row was judged."""
    d = _decision(conn, name)
    when = (sql.SQL("s.{}").format(sql.Identifier(by)) if by else sql.SQL("j.judged_at"))
    rows = conn.execute(sql.SQL(
        "SELECT j.value, j.score, {w} FROM docket.current c JOIN docket.judgment j ON j.id = c.judgment_id "
        "JOIN {t} s ON s.{k}::text = c.row_key WHERE c.decision_id = %s AND c.version = %s AND {w} IS NOT NULL").format(
        w=when, t=sql.SQL(d["source"]), k=sql.Identifier(d["key"])), (d["id"], d["version"])).fetchall()
    if not rows:
        return d, {"note": "no judged rows yet"}
    start = max(r[2] for r in rows) - timedelta(days=days)         # the recent window
    k, first = _series(conn, d, by or "judged_at")
    cut = _like(first["cut"], rows[0][2]) if first else start
    block = float(first.get("block_days", first["window_days"])) if first else float(days)
    ref = [r for r in rows if r[2] < cut]
    new = sorted((r for r in rows if r[2] >= cut), key=lambda r: r[2])
    recent = [r[2] >= start for r in new]
    out = {"window_days": days, "block_days": block, "by": by or "judged_at", "cut": cut.isoformat(),
           "window_start": start.isoformat(), "run": k}
    if len(ref) < MIN_ROWS or sum(recent) < MIN_ROWS:
        hint = "" if by else "; to date rows by a column of the table instead of when they were judged, pass --by COLUMN"
        return d, out | {"note": f"{sum(recent):,} rows from the last {days:g} days and {len(ref):,} before "
                                 + ("them" if cut == start else f"the series began ({cut:%Y-%m-%d})")
                                 + f": the check needs at least {MIN_ROWS} on each side{hint}"}
    day = [int((r[2] - cut) / timedelta(days=block)) + 1 for r in new]
    out |= rows_check([_answer(d["spec"], v, s) for v, s, _ in ref], [s for _, s, _ in ref],
                      [_answer(d["spec"], v, s) for v, s, _ in new], [s for _, s, _ in new], day, recent, k)
    out["rows_since_cut"] = len(new)
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


def _e(ev):
    """An e-value as printed: its size, or its power of ten when huge."""
    return f"{math.exp(ev['log_e']):.2g}" if ev["log_e"] < 690 else f"10^{ev['log_e'] / math.log(10):.0f}"


def verdict_data(r):
    pct = lambda x: f"{100 * x:.1f} %"
    if "note" in r:
        return [f"data: {r['note']}"]
    since = r["cut"][:10]
    first = r["cut"] == r.get("window_start", r["cut"])
    head = (f"data: {r['rows_new']:,} rows from the last {r['window_days']} days (by {r['by']}) against "
            f"{r['rows_ref']:,} " + ("earlier" if first else f"from before the series began ({since})")
            + f" · check {r['run']}")
    m, s = r["mix"], r["scores"]
    budget = f"needs p < {r['alpha'] / 2:.2g}"
    ev = lambda e, what: (f" · evidence since {since}: {_e(e)} on {what} (needs {math.exp(e['threshold']):.0f})"
                          if e else "")
    lines = [head,
             f"  answers: " + ("; ".join(f"{x['answer']} {pct(x['before'])} → {pct(x['after'])}" for x in m["moved"])
                               or "no answer common enough to test") + f" · p = {m['p']:.2g} ({budget})"
             + ev(m.get("evidence"), m.get("evidence", {}).get("item")) + ("  ⚠ shifted" if m["alarm"] else ""),
             f"  scores: KS distance {s['ks']:.3f}, mean {s['mean_before']:.3f} → {s['mean_after']:.3f}, p = {s['p']:.2g} "
             f"({budget})" + ev(s.get("evidence"), f"scores ≤ {s['evidence']['item']:.2f}" if s.get("evidence") else "")
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
