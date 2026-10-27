"""`pgdocket check`: how trustworthy is an AI decision on *your* table?

Samples labeled rows (read-only), judges them one row per request, and reports agreement with the labels,
calibration before and after a one-parameter calibrator, a threshold with a stated precision guarantee,
optional run-to-run stability, batching sensitivity and steerability (can a row's author steer the answer?), and
the projected cost for the whole table. Row text never appears in the report.
"""
import csv
import json
import os
import time
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

import numpy as np

from . import metrics as M
from .backend import USD_PER_INPUT_TOKEN, Backend


@dataclass
class CheckConfig:
    dsn: str
    table: str
    key: str
    text: list
    kind: str                                   # 'choice' or 'noul'
    question: str
    options: list = field(default_factory=list)            # choice: [{"name", "description"}]
    label_column: str = None
    label_file: str = None
    positive: str = None                        # noul: label value that means "yes"
    where: str = None
    sample: int = 300
    seed: str = "1"
    backend: str = "jev"
    model: str = "jev-1.14.0"
    batch_size: int = 1
    repeat: int = 0
    batch_test: int = 0
    steer: int = 0                              # rows to attack with author-written text (#24)
    untrusted: bool = False                     # tell the model the rows come from third parties (#24)
    precision_target: float = 0.9
    alpha: float = 0.05
    max_usd: float = 1.0
    out: str = None


# ---------------------------------------------------------------- data access (read-only)
def _ident(name):
    from psycopg import sql
    return sql.Identifier(*name.split("."))


def _connect(dsn):
    import psycopg
    conn = psycopg.connect(dsn)
    conn.read_only = True                      # pgdocket's check never writes to your database
    return conn


def option_names(cfg):
    return [o["name"] for o in cfg.options]


def option_display(cfg):
    """What the model sees: 'name: description' when a description is given."""
    return [f"{o['name']}: {o['description']}" if o.get("description") else o["name"] for o in cfg.options]


def _filter_sql(cfg):
    from psycopg import sql
    parts = []
    if cfg.label_column and not cfg.label_file:
        parts.append(sql.SQL("{} IS NOT NULL").format(sql.Identifier(cfg.label_column)))
        if cfg.kind == "choice":
            parts.append(sql.SQL("{}::text = ANY({})").format(sql.Identifier(cfg.label_column),
                                                               sql.Literal(option_names(cfg))))
    if cfg.where:
        parts.append(sql.SQL("(") + sql.SQL(cfg.where) + sql.SQL(")"))
    return sql.SQL(" WHERE ") + sql.SQL(" AND ").join(parts) if parts else sql.SQL("")


def read_sample(cfg):
    from psycopg import sql
    cols = [sql.SQL("{}::text").format(sql.Identifier(cfg.key))]
    cols += [sql.Identifier(c) for c in cfg.text]
    if cfg.label_column and not cfg.label_file:
        cols.append(sql.SQL("{}::text").format(sql.Identifier(cfg.label_column)))
    q = sql.SQL("SELECT {} FROM {}{} ORDER BY md5({}::text || {}) LIMIT {}").format(
        sql.SQL(", ").join(cols), _ident(cfg.table), _filter_sql(cfg), sql.Identifier(cfg.key),
        sql.Literal(":" + str(cfg.seed)), sql.Literal(cfg.sample))
    with _connect(cfg.dsn) as conn, conn.cursor() as cur:
        cur.execute(q)
        rows = cur.fetchall()
        cur.execute(sql.SQL("SELECT count(*) FROM {}{}").format(_ident(cfg.table), _filter_sql(cfg)))
        in_scope = cur.fetchone()[0]
        cur.execute(sql.SQL("SELECT count(*) FROM {}").format(_ident(cfg.table)))
        table_rows = cur.fetchone()[0]
    sample = []
    for r in rows:
        record = {c: ("" if v is None else str(v)) for c, v in zip(cfg.text, r[1:1 + len(cfg.text)])}
        label = r[1 + len(cfg.text)] if (cfg.label_column and not cfg.label_file) else None
        sample.append({"key": r[0], "record": record, "label": label})
    if cfg.label_file:
        labels = {}
        with open(cfg.label_file, newline="") as f:
            for row in csv.DictReader(f):
                if (row.get("label") or "").strip():
                    labels[row["key"]] = row["label"].strip()
        sample = [dict(s, label=labels[s["key"]]) for s in sample if s["key"] in labels]
        if cfg.kind == "choice":
            names = set(option_names(cfg))
            unknown = Counter(s["label"] for s in sample if s["label"] not in names)
            if unknown:
                print(f"skipping {sum(unknown.values())} rows whose label is not an option: {dict(unknown)}")
            sample = [s for s in sample if s["label"] in names]
    return sample, in_scope, table_rows


def export_sample(cfg, path):
    """Write a CSV with the sampled rows and an empty `label` column, for labeling by hand."""
    sample, _, _ = read_sample(CheckConfig(**dict(asdict(cfg), label_column=None, label_file=None)))
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["key"] + cfg.text + ["label"])
        for s in sample:
            w.writerow([s["key"]] + [s["record"][c] for c in cfg.text] + [""])
    return len(sample)


# ---------------------------------------------------------------- answers → arrays
def _choice_probs(answer, display):
    probs = answer.get("probabilities") or {}
    legend = answer.get("legend") or {}
    v = np.zeros(len(display))
    for k, p in probs.items():
        name = legend.get(k, k)
        if name in display:
            v[display.index(name)] = float(p)
        elif str(k).isdigit() and int(k) < len(display):
            v[int(k)] = float(p)
    if v.sum() <= 0 and answer.get("choice") in display:
        v[display.index(answer["choice"])] = 1.0
    return v / v.sum() if v.sum() > 0 else np.full(len(display), 1 / len(display))


def _floor(P):
    if not (P == 0).any():
        return P
    Q = np.maximum(P, P[P > 0].min() / 2)
    return Q / Q.sum(axis=1, keepdims=True)


# ---------------------------------------------------------------- analysis
LADDER = (0.95, 0.9, 0.85, 0.8, 0.75, 0.7)


def _ladder(score, correct, alpha, positives=None):
    """For each precision target: how many rows can be auto-accepted with that guarantee (None if none).
    For yes/no questions (`positives` given) also the share of all real positives caught (recall)."""
    out = []
    for t in LADDER:
        c = M.precision_threshold(score, correct, t, alpha)["chosen"]
        row = {"target": t, "threshold": c["threshold"] if c else None, "coverage": c["coverage"] if c else 0.0}
        if positives is not None:
            row["recall"] = (float(((score >= c["threshold"]) & (positives == 1)).sum() / max(positives.sum(), 1))
                             if c else 0.0)
        out.append(row)
    return out
def analyse_choice(cfg, sample, answers):
    names, display = option_names(cfg), option_display(cfg)
    P = np.array([_choice_probs(a, display) for a in answers])
    y = np.array([names.index(s["label"]) for s in sample])
    pred = P.argmax(axis=1)
    correct = (pred == y).astype(float)
    conf = P.max(axis=1)
    P_cal, T = M.crossfit_temperature(_floor(P), y)
    conf_cal = P_cal.max(axis=1)
    correct_cal = (P_cal.argmax(axis=1) == y).astype(float)
    confusions = Counter((names[t], names[p]) for t, p in zip(y, pred) if t != p).most_common(5)
    chosen = M.precision_threshold(conf, correct, cfg.precision_target, cfg.alpha)["chosen"]
    per = M.per_answer(conf, correct, np.array(names)[pred], chosen["threshold"] if chosen else None,
                       cfg.precision_target, cfg.alpha)                    # the overall guarantee, answer by answer (#19)
    own = {r["answer"]: r["own_threshold"] for r in per}
    per_cov = float(np.mean([own.get(names[p]) is not None and c >= own[names[p]] for p, c in zip(pred, conf)]))
    return {
        "per_answer": per, "per_answer_coverage": per_cov,
        "n": len(y), "accuracy": float(correct.mean()), "accuracy_ci": M.bootstrap_ci(np.mean, [correct]),
        "macro_f1": M.macro_f1(pred, y, len(names)),
        "per_class": [{"option": o, "support": int((y == c).sum()), "predicted": int((pred == c).sum()),
                       "recall": float(correct[y == c].mean()) if (y == c).any() else None,
                       "precision": float((y[pred == c] == c).mean()) if (pred == c).any() else None}
                      for c, o in enumerate(names)],
        "top_confusions": [{"label": a, "answer": b, "count": n} for (a, b), n in confusions],
        "ece_raw": M.ece(conf, correct, 15), "ece_raw_ci": M.bootstrap_ci(lambda a, b: M.ece(a, b, 15), [conf, correct]),
        "temperature": T, "ece_calibrated": M.ece(conf_cal, correct_cal, 15),
        "reliability_raw": M.reliability_bins(conf, correct, 10),
        "reliability_calibrated": M.reliability_bins(conf_cal, correct_cal, 10),
        "exact_zeros": int((P == 0).sum()), "label_prob_zero": int((P[np.arange(len(y)), y] == 0).sum()),
        # thresholds on the model's own score, which doesn't depend on the labels: the guarantee holds exactly and
        # is the one `pgdocket calibrate` stores and the view applies (#16)
        "threshold": M.precision_threshold(conf, correct, cfg.precision_target, cfg.alpha), "threshold_on": "raw score",
        "ladder": _ladder(conf, correct, cfg.alpha),
        "zeros_clamped": bool((P == 0).any()),
        "_top": pred,
    }


def analyse_noul(cfg, sample, answers):
    p = np.array([float(a["noul"]) for a in answers])
    y = np.array([s["label"] == cfg.positive for s in sample], float)
    p_cal = M.crossfit_binary(p, y, "platt")
    thr = M.precision_threshold(p, y, cfg.precision_target, cfg.alpha)          # on the raw P(yes), as above
    if thr["chosen"]:
        acc = p >= thr["chosen"]["threshold"]
        thr["chosen"]["recall"] = float((acc & (y == 1)).sum() / max(y.sum(), 1))
    prec, rec, f1 = M.prf(p >= 0.5, y.astype(bool))
    return {
        "n": len(y), "prevalence": float(y.mean()), "auroc": M.auroc(p, y), "auroc_ci": M.bootstrap_ci(M.auroc, [p, y]),
        "precision_at_0.5": prec, "recall_at_0.5": rec, "f1_at_0.5": f1,
        "ece_raw": M.ece(p, y, 15), "ece_raw_ci": M.bootstrap_ci(lambda a, b: M.ece(a, b, 15), [p, y]),
        "ece_calibrated": M.ece(p_cal, y, 15),
        "reliability_raw": M.reliability_bins(p, y, 10), "reliability_calibrated": M.reliability_bins(p_cal, y, 10),
        "threshold": thr, "threshold_on": "raw score", "ladder": _ladder(p, y, cfg.alpha, positives=y), "_p": p,
        "recall_ladder": [M.recall_threshold(p, y.astype(bool), t, cfg.alpha) for t in (0.95, 0.9, 0.8)],
    }


def steer_test(cfg, backend, sample, answers, threshold, opts):
    """Can a row's author steer the answer (#24)? Each row gets one paragraph per attack family, aimed at a random
    wrong answer, and is judged again. Then erase-and-check runs on the rows `auto` would accept: clean ones, to see
    how many it flags for nothing, and steered ones, to see how many it catches."""
    from . import steer as S
    names = option_names(cfg) if cfg.kind == "choice" else None
    desc = {o["name"]: o.get("description") or "" for o in cfg.options} if cfg.kind == "choice" else {}

    def read(a):
        if cfg.kind == "choice":
            v = _choice_probs(a, opts)
            return names[int(v.argmax())], float(v.max())
        p = float(a["noul"])
        return ("yes" if p >= 0.5 else "no"), p

    auto = lambda ans, sc: threshold is not None and sc >= threshold and (cfg.kind == "choice" or ans == "yes")
    rows = []
    for s, a in zip(sample, answers):
        label = s["label"] if cfg.kind == "choice" else ("yes" if s["label"] == cfg.positive else "no")
        ans, sc = read(a)
        target = S.pick_target(cfg.kind, names, {label, ans}, f"{cfg.seed}:{s['key']}")
        if target:
            rows.append({"record": s["record"], "clean": ans, "score": sc, "target": target})
    attacked = [(r, fam, S.inject(r["record"], cfg.text, S.attack(fam, cfg.kind, r["target"], desc.get(r["target"], ""))))
                for r in rows for fam in S.FAMILIES]
    got = [read(a) for a in backend.judge([rec for *_, rec in attacked], cfg.kind, cfg.question, opts, 1,
                                          untrusted=cfg.untrusted)]
    checks = [(None, r["clean"], r["record"]) for r in rows if auto(r["clean"], r["score"])]
    checks += [(fam, ans, rec) for (r, fam, rec), (ans, sc) in zip(attacked, got) if ans == r["target"] and auto(ans, sc)]
    variants = [(i, e) for i, (_, _, rec) in enumerate(checks) for e in S.erased(S.segments(rec, cfg.text), cfg.text)]
    erased = [read(a)[0] for a in backend.judge([e for _, e in variants], cfg.kind, cfg.question, opts, 1,
                                                untrusted=cfg.untrusted)] if variants else []
    flagged = [False] * len(checks)
    for (i, _), ans in zip(variants, erased):
        flagged[i] |= ans != checks[i][1]
    out = {"rows": len(rows), "threshold": threshold, "families": {}}
    clean_flags = [f for (fam, _, _), f in zip(checks, flagged) if fam is None]
    out["clean"] = {"auto": len(clean_flags) / max(len(rows), 1),
                    "erase_flagged": float(np.mean(clean_flags)) if clean_flags else None}
    for fam in S.FAMILIES:
        res = [(r, ans, sc) for (r, f, _), (ans, sc) in zip(attacked, got) if f == fam]
        caught = [f for (cf, _, _), f in zip(checks, flagged) if cf == fam]
        out["families"][fam] = {"changed": float(np.mean([ans != r["clean"] for r, ans, _ in res])),
                                "steered": float(np.mean([ans == r["target"] for r, ans, _ in res])),
                                "steered_auto": float(np.mean([ans == r["target"] and auto(ans, sc) for r, ans, sc in res])),
                                "erase_caught": float(np.mean(caught)) if caught else None}
    return out


# ---------------------------------------------------------------- run
def run_check(cfg, api_key=None, log=print, yes=False):
    t_start = time.time()
    sample, in_scope, table_rows = read_sample(cfg)
    if len(sample) < 30:
        raise SystemExit(f"only {len(sample)} labeled rows found; need at least 30 (100–200 recommended)")
    backend = Backend(cfg.backend, api_key=api_key, model=cfg.model)
    chars = sum(len(json.dumps(s["record"])) for s in sample)
    per_row = chars / len(sample) / 3.5 + 120 + (270 if cfg.batch_size == 1 else 270 / cfg.batch_size)
    m_steer = min(cfg.steer, len(sample))                # 4 attacks per row, then erase-and-check (≈ 6 calls a row)
    calls = len(sample) + min(cfg.repeat, len(sample)) + min(cfg.batch_test, len(sample)) + 22 * m_steer
    est = per_row * calls * USD_PER_INPUT_TOKEN
    log(f"{len(sample)} labeled rows sampled from {in_scope:,} in scope ({table_rows:,} in {cfg.table}); "
        f"{calls} judgments; estimated ≤ ${est:.3f}" + ("" if backend.paid else " (free backend)"))
    if backend.paid and est > cfg.max_usd:
        raise SystemExit(f"estimated ${est:.2f} exceeds --max-usd {cfg.max_usd}")
    if backend.paid and not yes:
        raise SystemExit("paid backend: re-run with --yes to confirm the estimate above")

    records = [s["record"] for s in sample]
    opts = option_display(cfg) if cfg.kind == "choice" else None
    answers = backend.judge(records, cfg.kind, cfg.question, opts, cfg.batch_size,
                            progress=lambda d, n: log(f"  judged {d}/{n} requests"), untrusted=cfg.untrusted)
    main_tokens = backend.stats["input_tokens"]
    analysis = (analyse_choice if cfg.kind == "choice" else analyse_noul)(cfg, sample, answers)

    def decisions(ans):
        if cfg.kind == "choice":
            return np.array([_choice_probs(a, opts).argmax() for a in ans])
        return np.array([float(a["noul"]) for a in ans])

    base = analysis.pop("_top" if cfg.kind == "choice" else "_p")
    stability = batching = None
    if cfg.repeat:
        m = min(cfg.repeat, len(sample))
        again = decisions(backend.judge(records[:m], cfg.kind, cfg.question, opts, cfg.batch_size, untrusted=cfg.untrusted))
        if cfg.kind == "choice":
            stability = {"rows": m, "changed": float((again != base[:m]).mean())}
        else:
            stability = {"rows": m, "changed": float(((again >= 0.5) != (base[:m] >= 0.5)).mean()),
                         "mean_abs_change": float(np.abs(again - base[:m]).mean())}
    if cfg.batch_test:
        m = min(cfg.batch_test, len(sample))
        batched = decisions(backend.judge(records[:m], cfg.kind, cfg.question, opts, 20, untrusted=cfg.untrusted))
        labels = [s["label"] for s in sample[:m]]
        if cfg.kind == "choice":
            names = option_names(cfg)
            y = np.array([names.index(l) for l in labels])
            batching = {"rows": m, "changed": float((batched != base[:m]).mean()),
                        "accuracy_single": float((base[:m] == y).mean()), "accuracy_batched": float((batched == y).mean())}
        else:
            batching = {"rows": m, "changed": float(((batched >= 0.5) != (base[:m] >= 0.5)).mean()),
                        "mean_abs_change": float(np.abs(batched - base[:m]).mean())}

    steering = None
    if m_steer:
        chosen = analysis["threshold"]["chosen"]
        steering = steer_test(cfg, backend, sample[:m_steer], answers[:m_steer], chosen["threshold"] if chosen else None, opts)

    tokens_per_row = main_tokens / len(sample)
    result = {
        "docket_version": __import__("pgdocket").__version__,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "config": {k: v for k, v in asdict(cfg).items() if k != "dsn"},
        "backend": {"url": backend.url, "model": cfg.model, "batch_size": cfg.batch_size},
        "sample": {"labeled_rows": len(sample), "rows_in_scope": in_scope, "table_rows": table_rows,
                   "label_counts": dict(Counter(s["label"] for s in sample).most_common())},
        "analysis": analysis, "stability": stability, "batching": batching, "steering": steering,
        "cost": {"requests": backend.stats["requests"], "input_tokens": backend.stats["input_tokens"],
                 "usd": backend.cost_usd(), "tokens_per_row": tokens_per_row,
                 "projected_usd_table": tokens_per_row * table_rows * USD_PER_INPUT_TOKEN,
                 "projected_usd_per_million_rows": tokens_per_row * 1e6 * USD_PER_INPUT_TOKEN,
                 "seconds": time.time() - t_start},
    }
    out = cfg.out or os.path.join("results", "check-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S"))
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(out, "report.json"), "w") as f:
        json.dump(result, f, indent=2, default=float)
    with open(os.path.join(out, "judgments.jsonl"), "w") as f:      # keys and answers only, no row text
        for s, a in zip(sample, answers):
            f.write(json.dumps({"key": s["key"], "label": s["label"], "answer": a}) + "\n")
    with open(os.path.join(out, "report.md"), "w") as f:
        f.write(render(result))
    return out, result


def reanalyze(out, precision_target=None):
    """Recompute the analysis from a check folder's judgments.jsonl (no API calls) and rewrite the report."""
    with open(os.path.join(out, "report.json")) as f:
        result = json.load(f)
    conf = dict(result["config"], dsn="")
    if precision_target is not None:
        conf["precision_target"] = precision_target
    cfg = CheckConfig(**conf)
    sample, answers = [], []
    with open(os.path.join(out, "judgments.jsonl")) as f:
        for line in f:
            j = json.loads(line)
            sample.append({"key": j["key"], "record": None, "label": j["label"]})
            answers.append(j["answer"])
    analysis = (analyse_choice if cfg.kind == "choice" else analyse_noul)(cfg, sample, answers)
    analysis.pop("_top", None)
    analysis.pop("_p", None)
    result["analysis"], result["config"] = analysis, {k: v for k, v in asdict(cfg).items() if k != "dsn"}
    result["reanalysed"] = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                            "docket_version": __import__("pgdocket").__version__}
    md = render(result)                               # before opening any file: a failure leaves the reports intact
    with open(os.path.join(out, "report.json"), "w") as f:
        json.dump(result, f, indent=2, default=float)
    with open(os.path.join(out, "report.md"), "w") as f:
        f.write(md)
    return result


# ---------------------------------------------------------------- report
def _pct(x):
    return "–" if x is None else f"{100 * x:.1f} %"


def _num(x):
    return "–" if x is None else f"{x:.3f}"


def _rel_table(rows):
    L = ["| confidence | rows | stated | observed |", "| --- | ---: | ---: | ---: |"]
    L += [f"| {lo:.1f}–{hi:.1f} | {n} | {mc:.2f} | {acc:.2f} |" for lo, hi, n, mc, acc in rows if n]
    return "\n".join(L)


def recommendations(r):
    a, cfg, out = r["analysis"], r["config"], []
    if a["ece_raw"] > 0.05 and a["ece_calibrated"] < a["ece_raw"] - 0.01:
        out.append(f"**Don't read the raw scores as probabilities.** Calibration error is {a['ece_raw']:.3f}; "
                   f"after a one-parameter calibrator fitted on these labels it is {a['ece_calibrated']:.3f}.")
    elif a["ece_raw"] > 0.05:
        out.append(f"**Treat the raw scores with care.** Calibration error is {a['ece_raw']:.3f}, and with "
                   f"{r['sample']['labeled_rows']} labels a calibrator doesn't improve it yet ({a['ece_calibrated']:.3f}). "
                   "Label more rows before relying on calibrated scores.")
    else:
        out.append(f"Raw scores are already reasonably calibrated here (error {a['ece_raw']:.3f}).")
    th = a["threshold"]
    if th["chosen"]:
        c = th["chosen"]
        extra = f", catching {_pct(c.get('recall'))} of the positives" if "recall" in c else ""
        score = (("model's top probability" if cfg["kind"] == "choice" else "model's yes-probability")
                 if a.get("threshold_on") == "raw score" else
                 ("calibrated confidence" if cfg["kind"] == "choice" else "calibrated probability"))
        out.append(f"**Auto-accept answers whose {score} is ≥ {c['threshold']:.2f}.** That covers {_pct(c['coverage'])} of rows{extra}, with precision "
                   f"≥ {_pct(th['target'])} at {100 * (1 - th['alpha']):.0f} % confidence (observed {_pct(c['precision'])}). "
                   "Send the rest to a person.")
    else:
        best = next((l for l in a.get("ladder", []) if l["coverage"] > 0), None)
        caught = f", catching {_pct(best['recall'])} of the real yeses" if best and "recall" in best else ""
        alt = (f" The highest target that can be guaranteed here is {_pct(best['target'])}, for {_pct(best['coverage'])} "
               f"of rows{caught} (see the table under Threshold)." if best else "")
        out.append(f"**No threshold reaches {_pct(th['target'])} precision with this many labels.**{alt} "
                   "Otherwise: label more rows, or rephrase the question.")
    weak = [p for p in a.get("per_answer", []) if p["below_target"]]
    if weak and th["chosen"]:
        listed = "; ".join(f"*{p['answer']}* ({p['right']} of {p['auto']} right, at most {_pct(p['upper'])})" for p in weak)
        out.append(f"**Don't auto-accept every answer.** The guarantee holds for the accepted rows together, but these "
                   f"answers are below {_pct(th['target'])}: {listed}. Send them to a person, label more of their rows, "
                   f"or give each answer its own guarantee (`pgdocket calibrate --per-answer`: {_pct(a['per_answer_coverage'])} "
                   "of rows here). See *Precision per answer*.")
    s = r.get("stability")
    if s and s["changed"] > 0.02:
        out.append(f"**Store answers; don't re-ask.** {_pct(s['changed'])} of answers changed when the same rows "
                   "were judged again.")
    b = r.get("batching")
    if b and b["changed"] > max(0.03, (s or {}).get("changed", 0) * 2):
        out.append(f"**Judge one row per request.** Sending 20 rows per request changed {_pct(b['changed'])} of answers.")
    st = r.get("steering")
    if st:
        worst = max(st["families"].items(), key=lambda kv: kv[1]["steered_auto"])
        if worst[1]["steered_auto"] > 0.02:
            out.append(f"**Rows can steer the answer.** A *{worst[0]}* paragraph written into the row sent "
                       f"{_pct(worst[1]['steered'])} of the tested rows to the answer its author chose, and `auto` accepted "
                       f"{_pct(worst[1]['steered_auto'])}. If outsiders write these rows, don't act on `auto` alone. See "
                       "*Steering*.")
    n = r["sample"]["labeled_rows"]
    if n < 200:
        out.append(f"This check used {n} labels. About 200 give tighter numbers; pgdocket's Phase 0 found 100–200 labels "
                   "capture most of the calibration gain.")
    return out


def render(r):
    cfg, a, c = r["config"], r["analysis"], r["cost"]
    L = [f"# pgdocket check: `{cfg['table']}`", "",
         f"*Question:* **{cfg['question']}**" + (" The instructions also tell the model the rows come from third "
                                                  "parties and to ignore instructions in them (`--untrusted`)."
                                                  if cfg.get("untrusted") else ""), "",
         f"- Text sent: `{', '.join(cfg['text'])}` · labels: "
         f"`{cfg['label_column'] or cfg['label_file']}`" + (f" (yes = `{cfg['positive']}`)" if cfg["kind"] == "noul" else ""),
         f"- Backend `{r['backend']['url']}` · model `{r['backend']['model']}` · {r['backend']['batch_size']} row(s) per request",
         f"- {r['sample']['labeled_rows']} labeled rows sampled from {r['sample']['rows_in_scope']:,} in scope "
         f"(table: {r['sample']['table_rows']:,} rows) · {r['created_at']} · docket {r['docket_version']}"
         + (f" · re-analysed {r['reanalysed']['at']} with docket {r['reanalysed']['docket_version']} (no API calls)"
            if r.get("reanalysed") else ""), "",
         "## Recommendations", ""]
    L += [f"{i}. {t}" for i, t in enumerate(recommendations(r), 1)]
    L += ["", "## Agreement with your labels", ""]
    if cfg["kind"] == "choice":
        L += ["| measure | value |", "| --- | ---: |",
              f"| accuracy | {_pct(a['accuracy'])} [{_pct(a['accuracy_ci'][0])}, {_pct(a['accuracy_ci'][1])}] |",
              f"| macro-F1 | {a['macro_f1']:.3f} |", "",
              "| option | labeled | answered | recall | precision |", "| --- | ---: | ---: | ---: | ---: |"]
        L += [f"| {p['option']} | {p['support']} | {p['predicted']} | {_pct(p['recall'])} | {_pct(p['precision'])} |"
              for p in a["per_class"]]
        if a["top_confusions"]:
            L += ["", "Most common disagreements (label → answer): " +
                  "; ".join(f"{x['label']} → {x['answer']} ({x['count']})" for x in a["top_confusions"])]
    else:
        L += ["| measure | value |", "| --- | ---: |",
              f"| share labeled yes | {_pct(a['prevalence'])} |",
              f"| ranking quality (AUROC) | {a['auroc']:.3f} [{a['auroc_ci'][0]:.3f}, {a['auroc_ci'][1]:.3f}] |",
              f"| precision / recall at the default 0.5 | {_pct(a['precision_at_0.5'])} / {_pct(a['recall_at_0.5'])} |"]
    L += ["", "## Calibration", "",
          f"Calibration error (ECE): **{a['ece_raw']:.3f}** raw [{a['ece_raw_ci'][0]:.3f}, {a['ece_raw_ci'][1]:.3f}] → "
          f"**{a['ece_calibrated']:.3f}** after calibration (out-of-fold"
          + ((f", zeros clamped, then temperature {a['temperature']:.2f}" if a.get("zeros_clamped")
              else f", temperature {a['temperature']:.2f}") if cfg["kind"] == "choice" else ", Platt scaling") + ").", ""]
    if cfg["kind"] == "choice" and a.get("label_prob_zero"):
        L += [f"The backend gave the labeled answer a probability of exactly 0 on {a['label_prob_zero']} rows; "
              "zeros are clamped before calibrating.", ""]
    L += ["Raw:", "", _rel_table(a["reliability_raw"]), "", "Calibrated:", "", _rel_table(a["reliability_calibrated"]), ""]
    th = a["threshold"]
    L += ["## Threshold", "",
          f"Thresholds were tested from strictest to loosest (accepting 5 %, 10 %, … of rows, starting at "
          f"{th.get('min_accept', 20)} rows so the target is provable); the loosest one whose "
          f"precision is ≥ {_pct(th['target'])} at {100 * (1 - th['alpha']):.0f} % confidence (one-sided Clopper–Pearson) "
          "is chosen. This assumes the labeled sample is representative of the rows it will be applied to."
          + (" Thresholds apply to the model's own score (top probability, or yes-probability), which doesn't depend "
             "on the labels, so the guarantee holds exactly; `pgdocket calibrate` stores the same kind of threshold."
             if a.get("threshold_on") == "raw score" else
             " Thresholds apply to cross-fitted calibrated scores (reports before 2026-10-22; re-run `pgdocket report`).")
          , "",
          "| threshold | accepted | coverage | precision | 95 % lower bound |", "| ---: | ---: | ---: | ---: | ---: |"]
    L += [f"| {t['threshold']:.3f} | {t['accepted']} | {_pct(t['coverage'])} | {_pct(t['precision'])} | {_pct(t['lower_bound'])} |"
          for t in th["tested"]]
    if a.get("ladder"):
        L += ["", "What each precision target allows (each with the same 95 % guarantee):", ""]
        if cfg["kind"] == "noul":
            L += ["| precision target | rows auto-labelled yes | real yeses caught (recall) | threshold |",
                  "| ---: | ---: | ---: | ---: |"]
            L += [f"| {_pct(l['target'])} | {_pct(l['coverage'])} | {_pct(l.get('recall'))} | {_num(l['threshold'])} |"
                  for l in a["ladder"]]
        else:
            L += ["| precision target | share of rows auto-accepted | threshold |", "| ---: | ---: | ---: |"]
            L += [f"| {_pct(l['target'])} | {_pct(l['coverage'])} | {_num(l['threshold'])} |" for l in a["ladder"]]
    if a.get("per_answer") and th["chosen"]:
        L += ["", "### Precision per answer", "",
              f"Rows the threshold accepts, by the model's answer, with one-sided 95 % Clopper–Pearson bounds. ⚠ marks "
              f"answers whose upper bound is below {_pct(th['target'])}. *Own guarantee* is what the answer would get "
              f"with its own threshold ({_pct(a['per_answer_coverage'])} of all rows together).", "",
              "| answer | answered | accepted | right | lower bound | upper bound | own guarantee accepts |",
              "| --- | ---: | ---: | ---: | ---: | ---: | ---: |"]
        L += [f"| {p['answer']}{' ⚠' if p['below_target'] else ''} | {p['answered']} | {p['auto']} | {_pct(p['precision'])} | "
              f"{_pct(p['lower'])} | {_pct(p['upper'])} | {p['own_auto']} |" for p in a["per_answer"]]
    if a.get("recall_ladder"):
        L += ["", "What each recall target allows: rows scored below the threshold can be answered *no* without review, "
                  "keeping at least that share of the real yeses (95 % confidence; fixed-sequence testing with one-sided "
                  "Clopper–Pearson bounds on the labelled yeses):", "",
              "| keep at least | rows answered *no* automatically | threshold | labelled yeses (needed) |",
              "| ---: | ---: | ---: | ---: |"]
        for rl in a["recall_ladder"]:
            ch = rl["chosen"]
            L.append(f"| {_pct(rl['target'])} of yeses | {_pct(ch['dismissed']) if ch else '–'} | {_num(ch['threshold']) if ch else '–'} | "
                     f"{rl['positives']} ({rl['positives_needed']}) |")
    s, b = r.get("stability"), r.get("batching")
    if s or b:
        L += ["", "## Consistency", "", "| test | rows | answers that changed |", "| --- | ---: | ---: |"]
        if s:
            L.append(f"| same request again | {s['rows']} | {_pct(s['changed'])} |")
        if b:
            L.append(f"| 20 rows per request instead of 1 | {b['rows']} | {_pct(b['changed'])} |")
        if b and "accuracy_batched" in b:
            L += ["", f"On those rows, accuracy with 1 row per request: {_pct(b['accuracy_single'])}; with 20: "
                      f"{_pct(b['accuracy_batched'])}."]
    st = r.get("steering")
    if st:
        L += ["", "## Steering", "",
              f"Can the person who writes a row steer its answer? Each of {st['rows']} rows got one paragraph appended per "
              "attack family, aimed at a random wrong answer. *Accepted by auto* uses the threshold above. Erase-and-check "
              "judges a row again without each of its pieces and flags it when an answer changes; *caught* is the share of "
              "steered, accepted rows it flagged.", "",
              "| attack | answer changed | sent where the author wanted | …and accepted by auto | erase-and-check caught |",
              "| --- | ---: | ---: | ---: | ---: |"]
        L += [f"| {fam} | {_pct(f['changed'])} | {_pct(f['steered'])} | {_pct(f['steered_auto'])} | {_pct(f['erase_caught'])} |"
              for fam, f in st["families"].items()]
        if st["clean"]["erase_flagged"] is not None:
            L += ["", f"On the clean rows `auto` accepts, erase-and-check flagged {_pct(st['clean']['erase_flagged'])} for nothing."]
    L += ["", "## Cost", "",
          f"- This check: {c['requests']:,} requests, {c['input_tokens']:,} input tokens, ≈ ${c['usd']:.4f}, {c['seconds']:.0f} s.",
          f"- {c['tokens_per_row']:.0f} tokens per row → whole table ({r['sample']['table_rows']:,} rows) ≈ "
          f"${c['projected_usd_table']:.2f}; ≈ ${c['projected_usd_per_million_rows']:.0f} per million rows "
          "(jev-1.13 list price).", "",
          "---", "*Generated by `pgdocket check`. The report contains no row text; per-row answers are in `judgments.jsonl` "
          "next to it, keyed by your primary key.*"]
    return "\n".join(L) + "\n"
