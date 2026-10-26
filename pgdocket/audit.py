"""Are the answers marked `auto` still right? A blind audit of accepted rows (issue #23)

The precision guarantee is measured once, on the labels used to calibrate. New rows can drift, and the drift check
(#22) says whether rows or the model changed, but only labels on new rows show whether the accepted answers are
still right. An audit draws a few rows at random from those the view marked `auto` since the previous batch.
People label them without seeing the AI's answer, because pre-annotations pull labellers towards the model
(Berzak et al., EMNLP 2016; Schroeder et al., 2025).

The test is an e-process (`metrics.eprocess_below`): it bets against "precision >= target", one audited row at a
time, in draw order. While every batch's precision is at least the target, the chance that its wealth ever reaches
1 / alpha is at most alpha (Ville's inequality), however often the audit is read and however many batches follow.
This is the sequential "harmful shift" test of Podkopaev & Ramdas (ICLR 2022), with the betting bounds of
Waudby-Smith & Ramdas (JRSS-B 2024). A series of batches belongs to one calibration: recalibrating starts a new one.

Audit labels are recorded as 'audit'. Drawn from accepted rows only, they are never used to calibrate, count or plan.
"""
import csv
import math
import os

import numpy as np
from psycopg import sql

from . import metrics as M

ALPHA = 0.05


def _decision(conn, name):
    r = conn.execute("SELECT d.id, d.active_version, d.source, d.key_column, d.text_columns, v.spec, v.calibration "
                     "FROM docket.decision d JOIN docket.version v ON v.decision_id = d.id AND v.version = d.active_version "
                     "WHERE d.name = %s", (name,)).fetchone()
    if not r:
        raise SystemExit(f"no decision named {name!r}")
    d = dict(zip(("id", "version", "source", "key", "text", "spec", "calibration"), r))
    if not d["calibration"] or d["calibration"].get("threshold") is None and not d["calibration"].get("thresholds"):
        raise SystemExit(f"{name} v{d['version']} marks no row `auto` yet: calibrate it first (`pgdocket calibrate {name}`)")
    return d


def draw(conn, name, n, seed="1"):
    """Draw up to n rows at random from those the view marks `auto` (yes/no: auto-yes), judged since the previous
    batch of this calibration and not labelled yet. Returns (batch, keys drawn, rows they were drawn from)."""
    d = _decision(conn, name)
    fitted = d["calibration"]["fitted_at"]
    batch, since = conn.execute(
        "SELECT coalesce(max(batch), 0) + 1, max(drawn_at) FROM docket.audit "
        "WHERE decision_id = %s AND version = %s AND fitted_at = %s", (d["id"], d["version"], fitted)).fetchone()
    yes = sql.SQL(" AND v.answer = 'yes'") if d["spec"]["kind"] == "noul" else sql.SQL("")
    rows = conn.execute(sql.SQL("""
        SELECT v.key::text, c.judgment_id FROM {view} v
        JOIN docket.current c ON c.decision_id = %(d)s AND c.version = %(v)s AND c.row_key = v.key::text
        JOIN docket.judgment j ON j.id = c.judgment_id
        WHERE v.auto{yes} AND j.judged_at > coalesce(%(since)s::timestamptz, '-infinity')
          AND NOT EXISTS (SELECT 1 FROM docket.label l WHERE l.decision_id = %(d)s AND l.row_key = v.key::text)
          AND NOT EXISTS (SELECT 1 FROM docket.audit a WHERE a.decision_id = %(d)s AND a.row_key = v.key::text)
        ORDER BY md5(v.key::text || %(seed)s)""").format(view=sql.Identifier("docket", "v_" + name), yes=yes),
        {"d": d["id"], "v": d["version"], "since": since, "seed": f":{seed}:{batch}"}).fetchall()
    picked = rows[:n]
    with conn.cursor() as cur:
        cur.executemany("INSERT INTO docket.audit (decision_id, row_key, version, fitted_at, batch, seq, judgment_id) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                        [(d["id"], k, d["version"], fitted, batch, i, j) for i, (k, j) in enumerate(picked, start=1)])
    return batch, [k for k, _ in picked], len(rows)


def label_from_column(conn, name, keys, column, positive=None):
    """Audit labels for drawn rows, from a column of the source table (when the labels already exist)."""
    d = _decision(conn, name)
    noul = d["spec"]["kind"] == "noul"
    if noul and positive is None:
        raise SystemExit("a yes/no decision needs --positive: the label value that means yes")
    col = sql.Identifier(column)
    value = (sql.SQL("CASE WHEN s.{}::text = %(pos)s THEN 'yes' ELSE 'no' END").format(col) if noul
             else sql.SQL("s.{}::text").format(col))
    return conn.execute(sql.SQL(
        "INSERT INTO docket.label (decision_id, row_key, label, source, sampling) "
        "SELECT %(d)s, s.{key}::text, {value}, %(src)s, 'audit' FROM {table} s "
        "WHERE s.{key}::text = ANY(%(keys)s) AND s.{col} IS NOT NULL ON CONFLICT (decision_id, row_key) DO NOTHING").format(
        key=sql.Identifier(d["key"]), value=value, table=sql.SQL(d["source"]), col=col),
        {"d": d["id"], "src": f"column:{column}", "keys": keys, "pos": None if positive is None else str(positive)}).rowcount


def export(conn, name, keys, path):
    """Write the drawn rows to label: key, the text columns, an empty label. Blind: no AI answer or confidence."""
    d = _decision(conn, name)
    q = sql.SQL("SELECT {k}::text, {cols} FROM {t} WHERE {k}::text = ANY(%s)").format(
        k=sql.Identifier(d["key"]), cols=sql.SQL(", ").join(sql.Identifier(c) for c in d["text"]), t=sql.SQL(d["source"]))
    text = {r[0]: r[1:] for r in conn.execute(q, (keys,)).fetchall()}
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["key"] + list(d["text"]) + ["label"])
        for k in keys:
            w.writerow([k] + list(text.get(k, [""] * len(d["text"]))) + [""])
    return len(keys)


def fetch(conn, name):
    """The current series: every row drawn under the active version's calibration, in draw order, with its label."""
    d = _decision(conn, name)
    rows = conn.execute("""
        SELECT a.batch, a.drawn_at, l.label, j.value FROM docket.audit a
        JOIN docket.judgment j ON j.id = a.judgment_id
        LEFT JOIN docket.label l ON l.decision_id = a.decision_id AND l.row_key = a.row_key
        WHERE a.decision_id = %s AND a.version = %s AND a.fitted_at = %s ORDER BY a.batch, a.seq""",
        (d["id"], d["version"], d["calibration"]["fitted_at"])).fetchall()
    noul = d["spec"]["kind"] == "noul"
    return d, [{"batch": b, "drawn_at": t, "labelled": lab is not None,
                "right": None if lab is None else (lab == "yes" if noul else lab == v)} for b, t, lab, v in rows]


def labels_to_detect(target, drop, alpha=ALPHA, trials=200, horizon=3000):
    """About how many audited rows show a fall from `target` to `target - drop`: the median, over simulated audits
    of a steady precision of target - drop, of the first row at which the test alarms."""
    rng = np.random.default_rng(0)
    first = []
    for _ in range(trials):
        w = M.eprocess_below(rng.uniform(size=horizon) < target - drop, target, stop=1 / alpha)
        first.append(len(w) if w[-1] >= 1 / alpha else math.inf)
    return math.ceil(float(np.median(first))) if np.median(first) < math.inf else None


def analyse(rows, target, alpha=ALPHA):
    x = [float(r["right"]) for r in rows if r["labelled"]]
    batches = {}
    for r in rows:
        b = batches.setdefault(r["batch"], {"batch": r["batch"], "drawn_at": r["drawn_at"], "drawn": 0, "labelled": 0, "right": 0})
        b["drawn"] += 1
        b["labelled"] += r["labelled"]
        b["right"] += bool(r["right"])
    out = {"target": target, "alpha": alpha, "batches": list(batches.values()), "drawn": len(rows), "labelled": len(x)}
    if not x:
        return out | {"alarm": False}
    w = M.eprocess_below(x, target)
    hit = np.flatnonzero(w >= 1 / alpha)
    labelled_batch = [r["batch"] for r in rows if r["labelled"]]
    return out | {"right": int(sum(x)), "precision": float(np.mean(x)),
                  "e_value": float(w[-1]), "e_max": float(w.max()), "alarm": bool(len(hit)),
                  "alarm_batch": labelled_batch[int(hit[0])] if len(hit) else None,
                  "detect_10pp": labels_to_detect(target, 0.10, alpha) if target > 0.1 else None}


def render(name, d, r):
    pct = lambda x: f"{100 * x:.1f} %"
    tgt = pct(r["target"])
    lines = [f"audit of {name} v{d['version']} (calibration of {d['calibration']['fitted_at'][:16].replace('T', ' ')}): "
             f"{len(r['batches'])} batch{'es' if len(r['batches']) != 1 else ''}, {r['drawn']} rows drawn, {r['labelled']} labelled"]
    for b in r["batches"]:
        share = f" ({pct(b['right'] / b['labelled'])})" if b["labelled"] else ""
        lines.append(f"  batch {b['batch']:<3} {b['drawn_at']:%Y-%m-%d %H:%M}  {b['drawn']:>4} drawn  {b['labelled']:>4} labelled  "
                     f"{b['right']:>4} right{share}")
    if any(b["labelled"] < b["drawn"] for b in r["batches"]):
        lines.append("  some drawn rows have no label yet: they are left out, which is safe only if no row is skipped "
                     "for being hard")
    if not r["labelled"]:
        return lines + ["  no audit labels yet: label the drawn rows and import them with `pgdocket label "
                        f"{name} --csv FILE`"]
    lines += [f"  precision of the audited rows: {pct(r['precision'])} ({r['right']} of {r['labelled']})",
              f"  evidence that it fell below the {tgt} target: e = {r['e_value']:.2g} (an alarm needs {1 / r['alpha']:.0f}; "
              "valid however often you look)"]
    if r["alarm"]:
        lines.append(f"  ⚠ the auto-accepted answers are below the {tgt} target (95 % confidence, from batch {r['alarm_batch']} "
                     "on): stop acting on `auto`, label a fresh random sample of recent rows and recalibrate")
    else:
        hint = f"; a fall to {pct(r['target'] - 0.1)} would show after about {r['detect_10pp']} audited rows" if r["detect_10pp"] else ""
        lines.append(f"  → no sign that the auto-accepted answers fell below {tgt}{hint}")
    return lines
