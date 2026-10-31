"""Turn one benchmark run into metrics.json + a Markdown report."""
import json
import os
from collections import defaultdict
from urllib.parse import urlsplit

import numpy as np

from . import metrics as M
from .db import connect
from .tasks import OTHER_OPTION, Truth, noul_truth

TAUS = (0.5, 0.7, 0.9)


# ---------------------------------------------------------------- loading
def latest_run_id(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT run_id FROM bench.runs WHERE finished_at IS NOT NULL ORDER BY started_at DESC LIMIT 1")
        r = cur.fetchone()
    if not r:
        raise SystemExit("no finished runs")
    return r[0]


def load_run(conn, run_id):
    with conn.cursor() as cur:
        cur.execute("SELECT run_id, backend, api_url, model, n_items, started_at, finished_at, notes, "
                    "coalesce((settings->>'batch_size')::int, 20) "
                    "FROM bench.runs WHERE run_id = %s", (run_id,))
        r = cur.fetchone()
        if not r:
            raise SystemExit(f"unknown run {run_id}")
        run = dict(zip(("run_id", "backend", "api_url", "model", "n_items", "started_at", "finished_at", "notes",
                        "batch_size"), r))
        cur.execute("SELECT experiment, kind, question, options, family, seconds, stats "
                    "FROM bench.experiment_runs WHERE run_id = %s ORDER BY experiment", (run_id,))
        exps = {e[0]: dict(zip(("name", "kind", "question", "options", "family", "seconds", "stats"), e))
                for e in cur.fetchall()}
        cur.execute("SELECT experiment, text_hash, answer FROM bench.results WHERE run_id = %s "
                    "ORDER BY experiment, text_hash", (run_id,))
        answers = defaultdict(dict)
        for e, h, a in cur.fetchall():
            answers[e][h] = a
        cur.execute("SELECT text_hash, product, coalesce(issue, '') FROM bench.complaints")
        truth = {h: Truth(p, i) for h, p, i in cur.fetchall()}
    return run, exps, answers, truth


def choice_probs(answer, options):
    """Probability vector aligned to `options`; accepts option-text or index keys (with a legend)."""
    probs = answer.get("probabilities") or {}
    legend = answer.get("legend") or {}
    v = np.zeros(len(options))
    for k, p in probs.items():
        name = legend.get(k, k)
        if name in options:
            v[options.index(name)] = float(p)
        elif str(k).isdigit() and int(k) < len(options):
            v[int(k)] = float(p)
    s = v.sum()
    if s <= 0:   # no distribution returned: put all mass on the reported choice
        v[options.index(answer["choice"])] = 1.0
        return v, False
    return v / s, abs(s - 1.0) < 1e-3


# ---------------------------------------------------------------- per-experiment analyses
def analyse_choice(exp, answers, truth):
    opts = list(exp["options"])
    hs = [h for h in answers if h in truth and truth[h].product in opts]
    P = np.array([choice_probs(answers[h], opts)[0] for h in hs])
    y = np.array([opts.index(truth[h].product) for h in hs])
    pred = P.argmax(axis=1)
    conf = P.max(axis=1)
    correct = (pred == y).astype(float)
    # Probabilities reported on a grid with exact zeros make NLL (and so temperature scaling) blow up on the few
    # rows whose label got 0. Floor them at half the reporting resolution before fitting a temperature.
    zeros = int((P == 0).sum())
    resolution = float(P[P > 0].min()) if (P > 0).any() else float("nan")
    floor = resolution / 2 if zeros else 0.0
    P_floor = np.maximum(P, floor)
    P_floor /= P_floor.sum(axis=1, keepdims=True)
    P_t, T = M.crossfit_temperature(P_floor, y)
    P_tu, T_u = M.crossfit_temperature(P, y)
    conf_iso = M.crossfit_binary(conf, correct, "isotonic")
    reported_conf = np.array([float(answers[h].get("confidence", np.nan)) for h in hs])
    p_true = P[np.arange(len(y)), y]
    out = {
        "n": len(hs), "k": len(opts),
        "accuracy": float(correct.mean()), "accuracy_ci": M.bootstrap_ci(np.mean, [correct]),
        "macro_f1": M.macro_f1(pred, y, len(opts)),
        "mean_confidence": float(conf.mean()),
        "ece": M.ece(conf, correct, 15), "ece_ci": M.bootstrap_ci(lambda c, k: M.ece(c, k, 15), [conf, correct]),
        "ece_mass": M.ece(conf, correct, 15, "mass"),
        "brier": M.brier_multi(P, y), "nll": M.nll_multi(P, y),
        "exact_zeros": zeros, "smallest_nonzero_prob": resolution, "probability_floor": floor,
        "rows_label_prob_zero": int((p_true == 0).sum()),
        "nll_floored": M.nll_multi(P_floor, y),
        "temperature": T,
        "ece_after_temperature": M.ece(P_t.max(axis=1), (P_t.argmax(axis=1) == y).astype(float), 15),
        "nll_after_temperature": M.nll_multi(P_t, y),
        "temperature_unfloored": T_u,
        "ece_after_temperature_unfloored": M.ece(P_tu.max(axis=1), (P_tu.argmax(axis=1) == y).astype(float), 15),
        "ece_after_isotonic": M.ece(conf_iso, correct, 15),
        "reliability": M.reliability_bins(conf, correct, 10),
        "per_class": [
            {"option": o, "support": int((y == c).sum()),
             "recall": float(correct[y == c].mean()) if (y == c).any() else None,
             "precision": float((y[pred == c] == c).mean()) if (pred == c).any() else None,
             "predicted": int((pred == c).sum())}
            for c, o in enumerate(opts)],
    }
    finite = ~np.isnan(reported_conf)
    if finite.sum() > 2 and np.std(reported_conf[finite]) > 0:
        out["reported_confidence_vs_max_prob_corr"] = float(np.corrcoef(reported_conf[finite], conf[finite])[0, 1])
    out["_arrays"] = {"hashes": hs, "P": P, "y": y}
    return out


def analyse_noul(exp, answers, truth):
    hs = [h for h in answers if h in truth]
    p = np.array([float(answers[h]["noul"]) for h in hs])
    y = np.array([noul_truth(exp["family"], truth[h]) for h in hs])
    yf = y.astype(float)
    iso = M.crossfit_binary(p, yf, "isotonic")
    platt = M.crossfit_binary(p, yf, "platt")
    prec, rec, f1 = M.prf(p >= 0.5, y)
    best_f1, best_t = M.best_f1_threshold(p, y)
    return {
        "n": len(hs), "prevalence": float(yf.mean()),
        "auroc": M.auroc(p, y), "auroc_ci": M.bootstrap_ci(M.auroc, [p, y]),
        "ece": M.ece(p, yf, 15), "ece_ci": M.bootstrap_ci(lambda a, b: M.ece(a, b, 15), [p, yf]),
        "ece_mass": M.ece(p, yf, 15, "mass"),
        "brier": M.brier_binary(p, yf), "nll": M.nll_binary(p, yf),
        "precision_at_0.5": prec, "recall_at_0.5": rec, "f1_at_0.5": f1,
        "best_f1": best_f1, "best_f1_threshold": best_t,
        "ece_after_isotonic": M.ece(iso, yf, 15), "brier_after_isotonic": M.brier_binary(iso, yf),
        "ece_after_platt": M.ece(platt, yf, 15), "brier_after_platt": M.brier_binary(platt, yf),
        "reliability": M.reliability_bins(p, yf, 10),
    }


def analyse_order(exps, per_exp, truth):
    """Base + cyclic shifts: flip rate, first-position lift, accuracy by true-label position, debiasing."""
    names = [n for n in ("choice_base",) + tuple(sorted(n for n in per_exp if n.startswith("choice_shift")))
             if n in per_exp]
    if len(names) < 2:
        return None
    canon = list(exps["choice_base"]["options"])
    common = set(per_exp[names[0]]["_arrays"]["hashes"])
    for n in names[1:]:
        common &= set(per_exp[n]["_arrays"]["hashes"])
    common = sorted(common)
    aligned = {}       # name -> (len(common), K) probabilities in canonical option order
    for n in names:
        opts = list(exps[n]["options"])
        arr = per_exp[n]["_arrays"]
        row = {h: i for i, h in enumerate(arr["hashes"])}
        perm = [opts.index(o) for o in canon]
        aligned[n] = arr["P"][[row[h] for h in common]][:, perm]
    y = np.array([canon.index(truth[h].product) for h in common])
    preds = np.stack([aligned[n].argmax(axis=1) for n in names], axis=1)
    flips = (preds != preds[:, :1]).any(axis=1).astype(float)

    first_pred, first_true = [], []
    lift = []          # per (row, option-that-is-first-somewhere): p(first) - mean p(not first)
    first_opt = {n: canon.index(exps[n]["options"][0]) for n in names}
    for n in names:
        pred_n = aligned[n].argmax(axis=1)
        first_pred.append((pred_n == first_opt[n]).astype(float))
        first_true.append((y == first_opt[n]).astype(float))
    # Within-class, within-row: for rows whose true class is listed first in one ordering, is the answer more often
    # right in that ordering than in the others? (Comparing all "truth first" rows with the rest would mix classes.)
    paired_gain, by_class = [], []
    for n in names:
        c = first_opt[n]
        rows = y == c
        if not rows.any():
            continue
        right_first = (aligned[n][rows].argmax(axis=1) == c).astype(float)
        right_other = np.mean([(aligned[m][rows].argmax(axis=1) == c) for m in names if first_opt[m] != c], axis=0)
        paired_gain.append(right_first - right_other)
        by_class.append({"option": canon[c], "n": int(rows.sum()), "recall_first": float(right_first.mean()),
                         "recall_not_first": float(right_other.mean())})
    paired_gain = np.concatenate(paired_gain) if paired_gain else np.array([np.nan])
    for n in names:
        c = first_opt[n]
        others = [aligned[m][:, c] for m in names if first_opt[m] != c]
        if others:
            lift.append(aligned[n][:, c] - np.mean(others, axis=0))
    lift = np.concatenate(lift) if lift else np.array([np.nan])
    excess = np.mean(first_pred, axis=0) - np.mean(first_true, axis=0)

    P_avg = np.mean([aligned[n] for n in names], axis=0)
    pred_avg = P_avg.argmax(axis=1)
    corr_avg = (pred_avg == y).astype(float)
    base = aligned["choice_base"]
    corr_base = (base.argmax(axis=1) == y).astype(float)
    return {
        "experiments": names, "n": len(common),
        "flip_rate": float(flips.mean()), "flip_rate_ci": M.bootstrap_ci(np.mean, [flips]),
        "first_position_pred_rate": float(np.mean(first_pred)),
        "first_position_true_rate": float(np.mean(first_true)),
        "first_position_excess": float(excess.mean()), "first_position_excess_ci": M.bootstrap_ci(np.mean, [excess]),
        "first_position_prob_lift": float(np.nanmean(lift)), "first_position_prob_lift_ci": M.bootstrap_ci(np.nanmean, [lift]),
        "recall_gain_when_first": float(np.nanmean(paired_gain)),
        "recall_gain_when_first_ci": M.bootstrap_ci(np.nanmean, [paired_gain]),
        "recall_by_position": by_class,
        "debiased_accuracy": float(corr_avg.mean()), "base_accuracy": float(corr_base.mean()),
        "debiased_accuracy_delta_ci": M.bootstrap_ci(np.mean, [corr_avg - corr_base]),
        "debiased_ece": M.ece(P_avg.max(axis=1), corr_avg, 15), "base_ece": M.ece(base.max(axis=1), corr_base, 15),
        "cost_multiplier": len(names),
    }


def analyse_drift(exps, per_exp, truth):
    if "choice_base" not in per_exp or "choice_other" not in per_exp:
        return None
    b, o = per_exp["choice_base"]["_arrays"], per_exp["choice_other"]["_arrays"]
    canon, opts_o = list(exps["choice_base"]["options"]), list(exps["choice_other"]["options"])
    rb, ro = {h: i for i, h in enumerate(b["hashes"])}, {h: i for i, h in enumerate(o["hashes"])}
    common = sorted(set(rb) & set(ro))
    Pb = b["P"][[rb[h] for h in common]]
    Po = o["P"][[ro[h] for h in common]]
    y = np.array([canon.index(truth[h].product) for h in common])
    pred_b = Pb.argmax(axis=1)
    pred_o_names = [opts_o[i] for i in Po.argmax(axis=1)]
    pred_b_names = [canon[i] for i in pred_b]
    flips = np.array([a != c for a, c in zip(pred_b_names, pred_o_names)], float)
    to_other = np.array([n == OTHER_OPTION for n in pred_o_names], float)
    p_true_b = Pb[np.arange(len(y)), y]
    p_true_o = Po[np.arange(len(y)), [opts_o.index(canon[i]) for i in y]]
    maxb, maxo = Pb.max(axis=1), Po.max(axis=1)
    correct_b = np.array([pb == canon[t] for pb, t in zip(pred_b_names, y)])
    correct_o = np.array([po == canon[t] for po, t in zip(pred_o_names, y)])
    auto = []
    for tau in TAUS:
        sb, so = maxb >= tau, maxo >= tau
        union = (sb | so).sum()
        auto.append({
            "tau": tau, "accepted_base": int(sb.sum()), "accepted_other": int(so.sum()),
            "jaccard": float((sb & so).sum() / union) if union else float("nan"),
            "precision_base": float(correct_b[sb].mean()) if sb.any() else None,
            "precision_other": float(correct_o[so].mean()) if so.any() else None,
        })
    # Threshold on the new label set that accepts as many rows as tau=0.7 did on the old one.
    n07 = int((maxb >= 0.7).sum())
    tau_equiv = float(np.sort(maxo)[::-1][n07 - 1]) if 0 < n07 <= len(maxo) else None
    return {
        "n": len(common), "flip_rate": float(flips.mean()), "flip_rate_ci": M.bootstrap_ci(np.mean, [flips]),
        "share_to_other": float(to_other.mean()),
        "mean_delta_p_true": float((p_true_o - p_true_b).mean()),
        "mean_delta_max_prob": float((maxo - maxb).mean()),
        "accuracy_base": float(correct_b.mean()), "accuracy_other": float(correct_o.mean()),
        "auto_accept": auto, "tau_equivalent_to_0.7": tau_equiv,
    }


# ---------------------------------------------------------------- orchestration
def analyse(run_id=None, out_root="results"):
    with connect() as conn:
        run_id = run_id if run_id and run_id != "latest" else latest_run_id(conn)
        run, exps, answers, truth = load_run(conn, run_id)
    per_exp = {}
    for name, e in exps.items():
        if e["kind"] == "choice":
            per_exp[name] = analyse_choice(e, answers[name], truth)
        elif e["kind"] == "noul":
            per_exp[name] = analyse_noul(e, answers[name], truth)
    order = analyse_order(exps, per_exp, truth)
    drift = analyse_drift(exps, per_exp, truth)
    sample = defaultdict(int)
    for t in truth.values():
        sample[t.product] += 1
    result = {
        "run": {k: (str(v) if v is not None else None) for k, v in run.items()},
        "sample": dict(sorted(sample.items(), key=lambda kv: -kv[1])),
        "experiments": {n: {k: v for k, v in r.items() if k != "_arrays"} for n, r in per_exp.items()},
        "experiment_meta": {n: {"question": e["question"], "options": e["options"], "seconds": e["seconds"],
                                "stats": e["stats"]} for n, e in exps.items()},
        "order_bias": order, "label_set_drift": drift,
    }
    out_dir = os.path.join(out_root, run_id)
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "metrics.json"), "w") as f:
        json.dump(result, f, indent=2, default=float)
    with open(os.path.join(out_dir, "report.md"), "w") as f:
        f.write(render(result))
    return out_dir, result


# ---------------------------------------------------------------- rendering
def _f(x, d=3):
    return "–" if x is None or (isinstance(x, float) and np.isnan(x)) else f"{x:.{d}f}"


def _pct(x, d=1):
    return "–" if x is None or (isinstance(x, float) and np.isnan(x)) else f"{100 * x:.{d}f} %"


def _ci(ci, pct=False, d=3):
    lo, hi = ci
    return f"[{_pct(lo) if pct else _f(lo, d)}, {_pct(hi) if pct else _f(hi, d)}]"


def _reliability_table(rows):
    lines = ["| bin | n | mean confidence | observed accuracy | gap |", "| --- | ---: | ---: | ---: | ---: |"]
    for lo, hi, n, mc, acc in rows:
        if n:
            lines.append(f"| {lo:.1f}–{hi:.1f} | {n} | {mc:.3f} | {acc:.3f} | {acc - mc:+.3f} |")
    return "\n".join(lines)


def render(r):
    run, ex = r["run"], r["experiments"]
    synthetic = run["backend"] in ("sim", "mock")
    L = [f"# Benchmark run `{run['run_id']}`", ""]
    if synthetic:
        L += ["> **Synthetic backend.** These numbers describe the harness, not a real model. The `sim` backend derives "
              "its answers from the labels with planted flaws (see `harness/jevbench/sim.py`); `mock` is pg-jev's "
              "deterministic test double.", ""]
    L += [f"- Backend: `{run['backend']}` · model `{run['model']}` · endpoint `{run['api_url']}` · "
          f"{run['batch_size']} row(s) per request",
          f"- Rows: {run['n_items']} CFPB complaint narratives · started {run['started_at']} · finished {run['finished_at']}",
          "- Ground truth: consumer-selected Product / Issue (noisy; agreement is a lower bound on fidelity)", ""]

    L += ["## Headline numbers", ""]
    head = []
    if "choice_base" in ex:
        c = ex["choice_base"]
        floored = f", zeros floored at {c['probability_floor']:g}" if c["probability_floor"] else ""
        head.append(f"- **Product choice ({c['k']} options):** accuracy {_pct(c['accuracy'])} "
                    f"{_ci(c['accuracy_ci'], True)}, top-label ECE {_f(c['ece'])} {_ci(c['ece_ci'])} → "
                    f"{_f(c['ece_after_temperature'])} after temperature scaling (T = {_f(c['temperature'], 2)}{floored}) and "
                    f"{_f(c['ece_after_isotonic'])} after isotonic (both cross-fitted).")
        if c["exact_zeros"]:
            head.append(f"- **Probability resolution:** smallest non-zero probability {c['smallest_nonzero_prob']:g}; "
                        f"{c['exact_zeros']:,} exact zeros; the labeled class gets exactly 0 on {c['rows_label_prob_zero']} rows. "
                        f"Unfloored temperature scaling fits T = {_f(c['temperature_unfloored'], 2)} and moves ECE to "
                        f"{_f(c['ece_after_temperature_unfloored'])}.")
    o = r.get("order_bias")
    if o:
        head.append(f"- **Option order:** {_pct(o['flip_rate'])} {_ci(o['flip_rate_ci'], True)} of rows change their "
                    f"answer across {len(o['experiments'])} cyclic orderings. Listing an option first lifts its "
                    f"probability by {_f(100 * o['first_position_prob_lift'], 1)} pp "
                    f"{_ci(tuple(100 * v for v in o['first_position_prob_lift_ci']), d=1)}; within-class recall changes by "
                    f"{_f(100 * o['recall_gain_when_first'], 1)} pp {_ci(tuple(100 * v for v in o['recall_gain_when_first_ci']), d=1)} "
                    f"when the true class is listed first. Averaging orderings: "
                    f"accuracy {_pct(o['base_accuracy'])} → {_pct(o['debiased_accuracy'])} at {o['cost_multiplier']}× cost.")
    d = r.get("label_set_drift")
    if d:
        a07 = next(a for a in d["auto_accept"] if a["tau"] == 0.7)
        head.append(f"- **Label-set drift (adding \"{OTHER_OPTION}\"):** {_pct(d['flip_rate'])} of top-1 answers change "
                    f"({_pct(d['share_to_other'])} move to the new option); at τ = 0.7 the auto-accepted set goes "
                    f"{a07['accepted_base']} → {a07['accepted_other']} rows (Jaccard {_f(a07['jaccard'], 2)}). "
                    f"Keeping the same volume needs τ ≈ {_f(d['tau_equivalent_to_0.7'], 2)}.")
    for name in sorted(n for n in ex if n.startswith("noul_")):
        n = ex[name]
        head.append(f"- **{name}:** AUROC {_f(n['auroc'])} {_ci(n['auroc_ci'])}, ECE {_f(n['ece'])} {_ci(n['ece_ci'])} "
                    f"→ {_f(n['ece_after_isotonic'])} isotonic / {_f(n['ece_after_platt'])} Platt (cross-fitted).")
    L += head + [""]

    L += ["## Sample", "", "| product | rows |", "| --- | ---: |"]
    L += [f"| {k} | {v} |" for k, v in r["sample"].items()] + [""]

    host = (urlsplit(r["run"]["api_url"]).hostname or "").lower()
    paid = host == "typesafe.ai" or host.endswith(".typesafe.ai")    # pg-jev prices every backend at Jev's rate
    L += ["## Cost and latency", "", "| experiment | seconds | requests | input tokens | est. cost (USD) |",
          "| --- | ---: | ---: | ---: | ---: |"]
    for name, m in sorted(r["experiment_meta"].items()):
        s = m["stats"] or {}
        L.append(f"| {name} | {_f(m['seconds'], 1)} | {s.get('requests', 0):.0f} | {s.get('input_tokens', 0):,.0f} | "
                 + (f"{s.get('estimated_cost_usd', 0):.4f} |" if paid else "none (free backend) |"))
    L.append("")

    for name in sorted(ex):
        m = ex[name]
        L += [f"## `{name}`", "", f"Question: *{r['experiment_meta'][name]['question']}*", ""]
        if "k" in m:
            L += [f"Options (in order): {', '.join(r['experiment_meta'][name]['options'])}", "",
                  "| metric | value |", "| --- | ---: |",
                  f"| n | {m['n']} |", f"| accuracy | {_pct(m['accuracy'])} {_ci(m['accuracy_ci'], True)} |",
                  f"| macro-F1 | {_f(m['macro_f1'])} |", f"| mean confidence | {_f(m['mean_confidence'])} |",
                  f"| ECE (15 equal-width bins) | {_f(m['ece'])} {_ci(m['ece_ci'])} |",
                  f"| ECE (15 equal-mass bins) | {_f(m['ece_mass'])} |",
                  f"| Brier / NLL | {_f(m['brier'])} / {_f(m['nll'])} |",
                  f"| smallest non-zero probability / exact zeros | {m['smallest_nonzero_prob']:g} / {m['exact_zeros']:,} |",
                  f"| rows where the labeled class has probability 0 | {m['rows_label_prob_zero']} |",
                  f"| ECE after temperature (T={_f(m['temperature'], 2)}, floor {m['probability_floor']:g}) | {_f(m['ece_after_temperature'])} |",
                  f"| ECE after temperature, unfloored (T={_f(m['temperature_unfloored'], 2)}) | {_f(m['ece_after_temperature_unfloored'])} |",
                  f"| ECE after isotonic | {_f(m['ece_after_isotonic'])} |"]
            if "reported_confidence_vs_max_prob_corr" in m:
                L.append(f"| corr(reported confidence, max prob) | {_f(m['reported_confidence_vs_max_prob_corr'])} |")
            L += ["", "| option | support | predicted | recall | precision |", "| --- | ---: | ---: | ---: | ---: |"]
            L += [f"| {c['option']} | {c['support']} | {c['predicted']} | {_pct(c['recall'])} | {_pct(c['precision'])} |"
                  for c in m["per_class"]]
        else:
            L += ["| metric | value |", "| --- | ---: |",
                  f"| n / prevalence | {m['n']} / {_pct(m['prevalence'])} |",
                  f"| AUROC | {_f(m['auroc'])} {_ci(m['auroc_ci'])} |",
                  f"| ECE (15 equal-width bins) | {_f(m['ece'])} {_ci(m['ece_ci'])} |",
                  f"| ECE (15 equal-mass bins) | {_f(m['ece_mass'])} |",
                  f"| Brier / NLL | {_f(m['brier'])} / {_f(m['nll'])} |",
                  f"| precision / recall / F1 at 0.5 | {_f(m['precision_at_0.5'])} / {_f(m['recall_at_0.5'])} / {_f(m['f1_at_0.5'])} |",
                  f"| best F1 (threshold) | {_f(m['best_f1'])} ({_f(m['best_f1_threshold'], 2)}) |",
                  f"| ECE / Brier after isotonic | {_f(m['ece_after_isotonic'])} / {_f(m['brier_after_isotonic'])} |",
                  f"| ECE / Brier after Platt | {_f(m['ece_after_platt'])} / {_f(m['brier_after_platt'])} |"]
        L += ["", "Reliability (raw):", "", _reliability_table(m["reliability"]), ""]

    if o:
        L += ["## Option-order sensitivity", "",
              f"Orderings: {', '.join(o['experiments'])} (cyclic shifts of the same options), {o['n']} rows.", "",
              "| metric | value |", "| --- | ---: |",
              f"| rows whose answer changes with order | {_pct(o['flip_rate'])} {_ci(o['flip_rate_ci'], True)} |",
              f"| answers that are the first-listed option | {_pct(o['first_position_pred_rate'])} |",
              f"| truths that are the first-listed option | {_pct(o['first_position_true_rate'])} |",
              f"| excess first-position answers | {_pct(o['first_position_excess'])} {_ci(o['first_position_excess_ci'], True)} |",
              f"| probability lift when listed first | {_f(100 * o['first_position_prob_lift'], 2)} pp |",
              f"| within-class recall gain when the true class is listed first | {_f(100 * o['recall_gain_when_first'], 2)} pp {_ci(tuple(100 * v for v in o['recall_gain_when_first_ci']), d=2)} |",
              f"| accuracy: canonical order → averaged over orderings | {_pct(o['base_accuracy'])} → {_pct(o['debiased_accuracy'])} (Δ CI {_ci(o['debiased_accuracy_delta_ci'], True)}) |",
              f"| ECE: canonical order → averaged | {_f(o['base_ece'])} → {_f(o['debiased_ece'])} |", "",
              "| class listed first | rows | recall when first | recall in other orderings |", "| --- | ---: | ---: | ---: |"]
        L += [f"| {b['option']} | {b['n']} | {_pct(b['recall_first'])} | {_pct(b['recall_not_first'])} |"
              for b in o["recall_by_position"]]
        L.append("")
    if d:
        L += ["## Label-set drift", "",
              f"Same question and order, with \"{OTHER_OPTION}\" appended. {d['n']} rows.", "",
              "| metric | value |", "| --- | ---: |",
              f"| top-1 answers that change | {_pct(d['flip_rate'])} {_ci(d['flip_rate_ci'], True)} |",
              f"| answers moving to the new option | {_pct(d['share_to_other'])} |",
              f"| mean Δ p(true label) | {_f(d['mean_delta_p_true'], 4)} |",
              f"| mean Δ max probability | {_f(d['mean_delta_max_prob'], 4)} |",
              f"| accuracy before → after | {_pct(d['accuracy_base'])} → {_pct(d['accuracy_other'])} |", "",
              "| τ | auto-accepted before | after | Jaccard | precision before | after |",
              "| ---: | ---: | ---: | ---: | ---: | ---: |"]
        L += [f"| {a['tau']} | {a['accepted_base']} | {a['accepted_other']} | {_f(a['jaccard'], 2)} | "
              f"{_pct(a['precision_base'])} | {_pct(a['precision_other'])} |" for a in d["auto_accept"]]
        L.append("")

    L += ["## Method notes", "",
          "- ECE is the count-weighted |accuracy − confidence| over bins (top-label confidence for choice). "
          "Brackets are 95 % percentile-bootstrap intervals over rows (1,000 resamples).",
          "- Recalibrated numbers are **out-of-fold** (5-fold cross-fitting), so they are not scored on the rows they were fitted on.",
          "- If a backend reports exact zeros, temperature scaling is fitted after flooring probabilities at half the smallest "
          "non-zero value; otherwise a handful of zero-probability labels dominate NLL and the fitted temperature.",
          "- Yes/no truth is a coded field (product or issue). A confident \"yes\" on a row coded otherwise can be a model error "
          "**or** a narrative that satisfies the question while the consumer filed it under another issue; raw ECE mixes both. "
          "Recalibrated ECE measures how well the scores map onto *this* label definition.",
          "- Sampling: CC0 mirror of CFPB narratives; the source, revision, seed and truncation are recorded in "
          "`data/raw/*.manifest.json` (default: uniform sample of parquet shard 0, de-duplicated by narrative, ≤ 1,500 chars).",
          "- Each experiment runs in a fresh session with `jev_cache_clear()`: pg-jev's answer cache key omits "
          "`jev.model` and `jev.api_url`.", ""]
    return "\n".join(L)
