"""Compare a shadow version of a decision with the active one, against the active one's own noise (issue #15).

On a sample of rows, three answers are available: the active version's live answer (old), the active version
re-asked as a probe (noise baseline), and the shadow version's answer (new).

- Changed answers: how often new differs from old, against how often old differs from itself when re-asked.
  Only the excess over that noise is caused by the change; it gets a paired bootstrap interval.
- Where answers move: the most common old → new transitions.
- Accuracy difference: where the two versions give the same answer, they are both right or both wrong, so only
  the rows where they disagree decide which is more accurate. Once every disagreement in the sample has a label
  (`--export-disagreements` lists them), the difference is known on every sampled row. Until then it is measured
  on random-sample labels only.
- Calibration: both versions fitted on the labels `calibrate` would use (random and whole-column), so it shows what
  `promote` would do; each one's calibration error, guaranteed threshold and the share of compared rows it would
  mark `auto`.
- A verdict: promote / keep current / no clear difference / needs labels.
"""
import csv
import json
import os
from collections import Counter
from datetime import datetime, timezone
from math import comb

import numpy as np
from psycopg import sql

from . import calibration
from . import metrics as M
from .spec import option_names, read_answer

MIN_LABELS = 30


def decision_value(spec, answer):
    """The answer a reader would see: the option name (choice) or yes/no at 0.5 (yes/no)."""
    if answer is None:
        return None
    value, score = read_answer(spec, answer)
    return value if spec["kind"] == "choice" else ("yes" if score >= 0.5 else "no")


def analyse(spec_old, spec_new, rows, n_boot=2000, seed=0, precision_target=0.9, per_answer=False, review=()):
    """rows: dicts with old, probe, new (raw answers), label (or None) and sampling ('random', 'manual', 'all').
    Rows missing an answer are skipped."""
    rows = [r for r in rows if r["old"] is not None and r["probe"] is not None and r["new"] is not None]
    n = len(rows)
    if n == 0:
        return {"n": 0, "verdict": "incomplete", "reasons": ["no row has all three answers yet"]}
    old = [decision_value(spec_old, r["old"]) for r in rows]
    probe = [decision_value(spec_old, r["probe"]) for r in rows]
    new = [decision_value(spec_new, r["new"]) for r in rows]
    label = [r.get("label") for r in rows]
    changed_new = np.array([a != b for a, b in zip(old, new)], float)
    changed_noise = np.array([a != b for a, b in zip(old, probe)], float)
    diff = changed_new - changed_noise
    out = {
        "n": n,
        "changed": float(changed_new.mean()), "noise": float(changed_noise.mean()),
        "excess": float(diff.mean()), "excess_ci": M.bootstrap_ci(np.mean, [diff], n_boot=n_boot, seed=seed),
        "transitions": [{"old": a, "new": b, "rows": c}
                        for (a, b), c in Counter((a, b) for a, b in zip(old, new) if a != b).most_common(8)],
        "counts_old": dict(Counter(old).most_common()), "counts_new": dict(Counter(new).most_common()),
    }
    tok_old = [r["tokens_old"] for r in rows if r.get("tokens_old")]
    tok_new = [r["tokens_new"] for r in rows if r.get("tokens_new")]
    if tok_old and tok_new:
        out["tokens_per_row_old"], out["tokens_per_row_new"] = float(np.mean(tok_old)), float(np.mean(tok_new))

    right_old = np.array([o == l for o, l in zip(old, label)], float)
    right_new = np.array([nw == l for nw, l in zip(new, label)], float)
    has_label = np.array([l is not None for l in label])
    is_random = has_label & np.array([r.get("sampling", "random") == "random" for r in rows])
    disagree = changed_new.astype(bool)
    out["labels"] = int(is_random.sum())
    out["disagreements"], out["disagreements_labelled"] = int(disagree.sum()), int((disagree & has_label).sum())
    if out["disagreements_labelled"]:
        m = disagree & has_label
        out["disagreements_right"] = {"current": int((right_old[m] > right_new[m]).sum()),
                                      "new": int((right_new[m] > right_old[m]).sum()),
                                      "neither": int((right_old[m] + right_new[m] == 0).sum())}
        out["sign_test_p"] = sign_test(out["disagreements_right"]["current"], out["disagreements_right"]["new"])
    if is_random.any():
        out["accuracy_old"], out["accuracy_new"] = float(right_old[is_random].mean()), float(right_new[is_random].mean())
    if out["disagreements_labelled"] == out["disagreements"]:
        gain = np.where(disagree, right_new - right_old, 0.0)          # agreeing rows: both right or both wrong
        out["gain_from"] = "disagreements"
    elif out["labels"]:
        gain = (right_new - right_old)[is_random]
        out["gain_from"] = "random labels"
    if "gain_from" in out:
        out["accuracy_gain"] = float(gain.mean())
        out["accuracy_gain_ci"] = M.bootstrap_ci(np.mean, [gain], n_boot=n_boot, seed=seed)
    for_cal = has_label & np.array([r.get("sampling", "random") in ("random", "all") for r in rows])   # as `calibrate`
    out["calibration_labels"] = int(for_cal.sum())
    if out["calibration_labels"] >= MIN_LABELS:
        out["calibration"] = {
            "current": _fit(spec_old, [r["old"] for r in rows], label, for_cal, precision_target, per_answer, review),
            "new": _fit(spec_new, [r["new"] for r in rows], label, for_cal, precision_target, per_answer, review)}
    if spec_new["kind"] == "choice" and has_label.any():
        unknown = sum(1 for l in label if l is not None and l not in option_names(spec_new))
        if unknown:
            out["labels_not_options_of_new"] = unknown
    out.update(_verdict(out))
    return out


def _fit(spec, answers, labels, use, precision_target, per_answer=False, review=()):
    """Fit a version's calibrator on the labels `calibrate` would use, in the active version's mode; then how many
    compared rows its view would mark `auto`, and which answers are below the target. None when labels can't fit."""
    idx = [i for i in range(len(answers)) if use[i]]
    y = [labels[i] if spec["kind"] == "choice" else labels[i] == "yes" for i in idx]
    try:
        cal = calibration.fit(spec, [answers[i] for i in idx], y, precision_target, per_answer=per_answer)
    except ValueError:
        return None
    cal["review"] = [x for x in review if x in calibration.answers(spec)]       # the active version's switch (#46)
    certified = (any(t is not None for t in cal["thresholds"].values()) if cal.get("mode") == "per answer"
                 else cal["threshold"] is not None)
    auto = float(np.mean([calibration.auto(a, spec, cal) for a in answers])) if certified else None
    return {"n_labels": cal["n_labels"], "precision_target": precision_target, "ece_raw": cal["ece_raw"],
            "ece_calibrated": cal["ece_calibrated"], "threshold": cal["threshold"], "auto_share": auto,
            "mode": cal.get("mode", "overall"), "thresholds": cal.get("thresholds"),
            "below_target": [r["answer"] for r in cal.get("per_answer", []) if r["below_target"]]}


def _rows(n):
    return f"{n} row" if n == 1 else f"{n} rows"


def sign_test(a, b):
    """Two-sided exact sign test: a wins against b wins, with ties (neither right) left out."""
    k = a + b
    return 1.0 if k == 0 else min(1.0, 2 * sum(comb(k, i) for i in range(min(a, b) + 1)) / 2 ** k)


def _verdict(r):
    pct = lambda x: f"{100 * x:.1f} %"
    pp = lambda x: f"{100 * x:+.1f} pp"
    reasons = []
    lo, hi = r["excess_ci"]
    if lo > 0:
        reasons.append(f"The new version changes {pct(r['changed'])} of answers, against {pct(r['noise'])} when the current "
                       f"one is simply asked again: {pct(r['excess'])} more [{pct(lo)}, {pct(hi)}] are due to the change.")
    else:
        reasons.append(f"The new version changes {pct(r['changed'])} of answers, against {pct(r['noise'])} when the current "
                       f"one is asked again: the difference [{pct(lo)}, {pct(hi)}] is within run-to-run noise.")
    gain_from = r.get("gain_from")
    if gain_from == "random labels" and r["labels"] < MIN_LABELS:
        gain_from = None
    if gain_from:
        glo, ghi = r["accuracy_gain_ci"]
        if gain_from == "disagreements" and not r["disagreements"]:
            basis = "The versions give the same answer on every row, so they are equally accurate here. "
        elif gain_from == "disagreements":
            w = r["disagreements_right"]
            basis = (f"The versions disagree on {r['disagreements']} of {r['n']} rows, all labelled: the current one was "
                     f"right on {w['current']}, the new one on {w['new']}, neither on {w['neither']} "
                     f"(exact sign test p = {r['sign_test_p']:.2f}). ")
        else:
            basis = f"On {r['labels']} random-sample labels: {pct(r['accuracy_old'])} → {pct(r['accuracy_new'])}. "
        acc = basis + f"Accuracy difference: {pp(r['accuracy_gain'])} [{pp(glo)}, {pp(ghi)}]."
        if glo > 0:
            verdict = "promote"
            reasons.append(acc + " The new version is more accurate.")
        elif ghi < 0:
            verdict = "keep current"
            reasons.append(acc + " The new version is less accurate.")
        else:
            verdict = "no clear difference"
            reasons.append(acc + f" No difference is detectable: the new version is between {100 * max(0.0, -glo):.1f} pp less "
                                 f"and {100 * ghi:.1f} pp more accurate.")
        if r.get("labels_not_options_of_new"):
            reasons.append(f"{r['labels_not_options_of_new']} labels are not options of the new version and count as wrong for it.")
    else:
        verdict = "needs labels"
        todo = r["disagreements"] - r["disagreements_labelled"]
        reasons.append(f"Which version is right can't be judged yet: {r['labels']} random-sample labels (need {MIN_LABELS}+), "
                       f"{r['disagreements_labelled']} of {r['disagreements']} disagreements labelled. Labelling the {_rows(todo)} "
                       "where the versions disagree is enough (`--export-disagreements`).")
    if verdict == "no clear difference" and r.get("gain_from") == "random labels" and r["disagreements"]:
        todo = r["disagreements"] - r["disagreements_labelled"]
        reasons.append(f"To narrow this, label the {_rows(todo)} where the versions disagree (`--export-disagreements`): "
                       f"that measures the difference on all {r['n']} rows.")
    c = r.get("calibration") or {}
    if c.get("current") and c.get("new"):
        share = lambda x: "none (no threshold reaches it)" if x["auto_share"] is None else pct(x["auto_share"])
        reasons.append(f"Auto-accept at ≥ {100 * c['current']['precision_target']:.0f} % precision (95 % confidence), each "
                       f"version calibrated on the same {r['calibration_labels']} labels as `calibrate` would use: current "
                       f"{share(c['current'])} of compared rows, "
                       f"new {share(c['new'])}.")
        weak = {k: ", ".join(c[k]["below_target"]) or "none" for k in ("current", "new")}
        if c["current"]["below_target"] or c["new"]["below_target"]:
            reasons.append(f"Answers whose auto-accepted rows are demonstrably below the target: current: {weak['current']}; "
                           f"new: {weak['new']}.")
        if r["calibration_labels"] < 150 and (c["current"]["auto_share"] is None) != (c["new"]["auto_share"] is None):
            reasons.append(f"With {r['calibration_labels']} labels, whether a guarantee can be certified at all is partly "
                           "chance: on Kubernetes routing, 80 random labels certified 90 % in only 54–66 % of draws. "
                           "Label more rows (150–200) before reading this as a difference between the versions.")
    if "tokens_per_row_new" in r:
        to, tn = r["tokens_per_row_old"], r["tokens_per_row_new"]
        cost = f"Tokens per row: {to:.0f} → {tn:.0f} ({100 * (tn / to - 1):+.0f} %)."
        if verdict == "no clear difference" and tn < to and r["accuracy_gain_ci"][0] < 0:
            cost += (f" Promote for the saving only if losing up to {100 * -r['accuracy_gain_ci'][0]:.1f} pp of accuracy "
                     "is acceptable.")
        reasons.append(cost)
    return {"verdict": verdict, "reasons": reasons}


# ---------------------------------------------------------------- database
def fetch(conn, name, version):
    d = conn.execute("SELECT d.id, d.active_version, d.source, d.key_column, d.text_columns, v.calibration "
                     "FROM docket.decision d JOIN docket.version v ON v.decision_id = d.id AND v.version = d.active_version "
                     "WHERE d.name = %s", (name,)).fetchone()
    if not d:
        raise SystemExit(f"no decision named {name!r}")
    did, active = d[0], d[1]
    specs = dict(conn.execute("SELECT version, spec FROM docket.version WHERE decision_id = %s AND version = ANY(%s)",
                              (did, [active, version])).fetchall())
    if version not in specs:
        raise SystemExit(f"{name} has no version {version}")
    # each row's noise baseline is the re-ask of the active version made closest in time to the new version's answer,
    # so a later comparison (which re-asks again) doesn't change this one's report
    rows = conn.execute("""
        SELECT co.row_key, jo.answer, pr.answer, jn.answer, l.label, l.sampling, jo.tokens, jn.tokens
        FROM docket.current co
        JOIN docket.judgment jo ON jo.id = co.judgment_id
        LEFT JOIN docket.current cn ON cn.decision_id = %(d)s AND cn.version = %(v)s AND cn.row_key = co.row_key
        LEFT JOIN docket.judgment jn ON jn.id = cn.judgment_id
        JOIN LATERAL (
            SELECT j.answer FROM docket.judgment j
            WHERE j.decision_id = %(d)s AND j.version = %(a)s AND j.purpose = 'probe' AND j.row_key = co.row_key
            ORDER BY abs(extract(epoch FROM j.judged_at - coalesce(jn.judged_at, now()))), j.id DESC
            LIMIT 1) pr ON true
        LEFT JOIN docket.label l ON l.decision_id = %(d)s AND l.row_key = co.row_key
        WHERE co.decision_id = %(d)s AND co.version = %(a)s
        ORDER BY co.row_key""", {"d": did, "a": active, "v": version}).fetchall()
    pending = pending_count(conn, did, active, version)
    cols = ("row_key", "old", "probe", "new", "label", "sampling", "tokens_old", "tokens_new")
    return {"decision_id": did, "active": active, "version": version, "spec_old": specs[active], "spec_new": specs[version],
            "source": d[2], "key": d[3], "text": d[4], "calibration": d[5], "rows": [dict(zip(cols, r)) for r in rows],
            "pending": pending}


def pending_count(conn, decision_id, active, version):
    """Comparison judgments still queued (not dead-lettered): the shadow version's, and the active version's re-asks."""
    return conn.execute(
        "SELECT count(*) FROM docket.queue WHERE decision_id = %s AND attempts < 5 AND "
        "((version = %s AND purpose = 'live') OR (version = %s AND purpose = 'probe'))",
        (decision_id, version, active)).fetchone()[0]


def export_disagreements(conn, data, path):
    """Write the compared rows where the versions disagree and no label exists yet: key, text, both answers, label."""
    so, sn = data["spec_old"], data["spec_new"]
    todo = {r["row_key"]: (decision_value(so, r["old"]), decision_value(sn, r["new"])) for r in data["rows"]
            if r["new"] is not None and r["label"] is None and decision_value(so, r["old"]) != decision_value(sn, r["new"])}
    q = sql.SQL("SELECT {k}::text, {cols} FROM {t} WHERE {k}::text = ANY(%s) ORDER BY {k}").format(
        k=sql.Identifier(data["key"]), cols=sql.SQL(", ").join(sql.Identifier(c) for c in data["text"]),
        t=sql.SQL(data["source"]))
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["key"] + list(data["text"]) + [f"v{data['active']}", f"v{data['version']}", "label"])
        for key, *text in conn.execute(q, (list(todo),)).fetchall():
            w.writerow([key] + text + list(todo[key]) + [""])
    return len(todo)


def store(conn, decision_id, version, result):
    conn.execute("UPDATE docket.version SET comparison = %s::jsonb WHERE decision_id = %s AND version = %s",
                 (json.dumps(result, default=float), decision_id, version))


def render(name, data, r):
    pct = lambda x: "–" if x is None else f"{100 * x:.1f} %"
    pp = lambda x: f"{100 * x:+.1f} pp"
    so, sn = data["spec_old"], data["spec_new"]
    L = [f"# Comparing `{name}` v{data['active']} (active) with v{data['version']} (shadow)", "",
         f"*{datetime.now(timezone.utc).isoformat(timespec='seconds')}* · {r['n']} rows compared", "",
         f"## Verdict: **{r['verdict']}**", ""]
    L += [f"- {x}" for x in r["reasons"]]
    L += ["", "## What changed in the spec", "", "| | v%s (active) | v%s (shadow) |" % (data["active"], data["version"]),
          "| --- | --- | --- |"]
    for key in ("question", "model", "backend", "batch_size"):
        if so.get(key) != sn.get(key):
            L.append(f"| {key} | {so.get(key)} | {sn.get(key)} |")
    L += [f"| {k} | {a} | {b} |" for k, a, b in _option_changes(so, sn)]
    L += ["", "## Answers that change", "", "| | share of rows |", "| --- | ---: |",
          f"| new version vs current | {pct(r['changed'])} |",
          f"| current asked again (noise) | {pct(r['noise'])} |",
          f"| due to the change | {pct(r['excess'])} [{pct(r['excess_ci'][0])}, {pct(r['excess_ci'][1])}] |", ""]
    if r["transitions"]:
        L += ["| current answer | new answer | rows |", "| --- | --- | ---: |"]
        L += [f"| {t['old']} | {t['new']} | {t['rows']} |" for t in r["transitions"]]
        L.append("")
    L += ["## Accuracy", "", "| | current | new | difference |", "| --- | ---: | ---: | ---: |"]
    if r.get("accuracy_old") is not None:
        L.append(f"| on {r['labels']} random-sample labels | {pct(r['accuracy_old'])} | {pct(r['accuracy_new'])} | "
                 f"{pp(r['accuracy_new'] - r['accuracy_old'])} |")
    if r.get("disagreements_right"):
        w = r["disagreements_right"]
        L.append(f"| right where they disagree ({r['disagreements_labelled']} of {r['disagreements']} labelled) | "
                 f"{w['current']} | {w['new']} | neither: {w['neither']} · sign test p = {r['sign_test_p']:.2f} |")
    if r.get("gain_from"):
        L.append(f"| **difference, 95 % interval** ({r['gain_from']}) | | | {pp(r['accuracy_gain'])} "
                 f"[{pp(r['accuracy_gain_ci'][0])}, {pp(r['accuracy_gain_ci'][1])}] |")
    L.append("")
    c = r.get("calibration") or {}
    if c.get("current") or c.get("new"):
        cell = lambda x, f: "–" if not x else f(x)
        thr = lambda x: (f"per answer: {sum(t is not None for t in x['thresholds'].values())} of {len(x['thresholds'])} "
                         "answers" if x.get("mode") == "per answer" else
                         "none" if x["threshold"] is None else f"{x['threshold']:.3f}")
        auto = lambda x: "–" if x["auto_share"] is None else pct(x["auto_share"])
        tgt = 100 * (c["current"] or c["new"])["precision_target"]
        L += [f"## Calibration on the same {r['calibration_labels']} labels (as `calibrate` and `promote` would use them)", "", "| | current | new |", "| --- | ---: | ---: |",
              "| calibration error, raw → calibrated | " + " | ".join(
                  cell(x, lambda x: f"{x['ece_raw']:.3f} → {x['ece_calibrated']:.3f}") for x in (c["current"], c["new"])) + " |",
              f"| threshold for ≥ {tgt:.0f} % precision (95 % confidence) | "
              + " | ".join(cell(x, thr) for x in (c["current"], c["new"])) + " |",
              "| compared rows it would auto-accept | " + " | ".join(cell(x, auto) for x in (c["current"], c["new"])) + " |",
              "| answers below the target | " + " | ".join(
                  cell(x, lambda x: ", ".join(x.get("below_target", [])) or "none") for x in (c["current"], c["new"])) + " |",
              ""]
    keys = sorted(set(r["counts_old"]) | set(r["counts_new"]), key=lambda k: -r["counts_old"].get(k, 0))
    L += ["## Answer counts in the sample", "", "| answer | current | new |", "| --- | ---: | ---: |"]
    L += [f"| {k} | {r['counts_old'].get(k, 0)} | {r['counts_new'].get(k, 0)} |" for k in keys]
    L += ["", "Intervals are 95 % paired bootstrap intervals over rows. \"Noise\" is the current version re-asked on "
          "the same rows (probe judgments), so only the excess over it is attributable to the change. Where both "
          "versions give the same answer they are equally right, so the accuracy difference depends only on the rows "
          "where they disagree."]
    return "\n".join(L) + "\n"


def _option_changes(so, sn):
    """Rows for the spec table: options added, removed or re-described, in a few words each."""
    a = {o["name"]: o.get("description") or "" for o in so.get("options", [])}
    b = {o["name"]: o.get("description") or "" for o in sn.get("options", [])}
    if a == b:
        return []
    described = lambda d: f"{len(d)} options, {sum(1 for v in d.values() if v)} with descriptions"
    out = [("options", described(a), described(b))]
    if set(a) - set(b) or set(b) - set(a):
        out.append(("names", ", ".join(sorted(set(a) - set(b))) or "–", ", ".join(sorted(set(b) - set(a))) or "–"))
    changed = sorted(k for k in set(a) & set(b) if a[k] != b[k])
    if changed and len(changed) <= 3:
        out += [(f"`{k}`", a[k] or "–", b[k] or "–") for k in changed]
    elif changed:
        out.append(("descriptions changed", f"{len(changed)} options", ", ".join(changed)))
    return out


def write(out_dir, md, result):
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "report.md"), "w") as f:
        f.write(md)
    with open(os.path.join(out_dir, "report.json"), "w") as f:
        json.dump(result, f, indent=2, default=float)
    return os.path.join(out_dir, "report.md")
