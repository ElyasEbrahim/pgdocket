"""Fit a one-parameter calibrator from human labels (ADR 0003) and apply it exactly as docket.calibrated() does in SQL.

choice: clamp exact zeros at half the smallest non-zero probability, then one temperature T.
noul:   Platt scaling, sigmoid(a * logit(p) + b).
The stored calibration also carries a threshold with a precision guarantee. It is chosen on the model's own score
(the top probability, or the yes-probability), which doesn't depend on the labels, so fixed-sequence testing holds
exactly and the view applies the very threshold that was tested (`threshold_on: "raw score"`). Calibrations stored
before 2026-10-22 chose it on cross-fitted calibrated scores and keep being applied that way.
"""
import math
from datetime import datetime, timezone

import numpy as np

from . import metrics as M
from .spec import choice_vector, option_display, option_names, read_answer


def calibrated(answer, spec, cal):
    """Python twin of the SQL function docket.calibrated(answer, spec, cal)."""
    if answer is None:
        return None
    if spec["kind"] == "noul":
        p = float(answer["noul"])
        if not cal:
            return p
        p = min(max(p, 1e-6), 1 - 1e-6)
        return 1 / (1 + math.exp(-(cal["a"] * math.log(p / (1 - p)) + cal["b"])))
    probs = [float(v) for v in (answer.get("probabilities") or {}).values()]
    if not cal:
        return max(probs)
    t, f = cal["temperature"], cal.get("floor") or 0.0
    q = [max(p, f) for p in probs]
    return max(q) ** (1 / t) / sum(x ** (1 / t) for x in q)


def raw_score(answer, spec):
    """The model's own score, as the worker stores it in judgment.score: top probability (choice) or P(yes) (noul)."""
    return read_answer(spec, answer)[1]


def auto(answer, spec, cal):
    """Python twin of the view's `auto` column."""
    if answer is None or not cal:
        return None
    if spec["kind"] == "noul" and cal.get("recall_threshold") is not None:     # auto-yes, auto-no, review band (#21)
        s = raw_score(answer, spec)
        return (cal.get("threshold") is not None and s >= cal["threshold"]) or s < cal["recall_threshold"]
    if "thresholds" in cal:                                     # one guarantee per answer (#19)
        value, score = read_answer(spec, answer)
        t = cal["thresholds"].get(value)
        return t is not None and score >= t
    if cal.get("threshold") is None:
        return False
    if cal.get("threshold_on") == "raw score":
        return raw_score(answer, spec) >= cal["threshold"]
    return calibrated(answer, spec, cal) >= cal["threshold"]


def noul_answer(answer, spec, cal):
    """Python twin of the view's `answer` for yes/no decisions."""
    s = raw_score(answer, spec)
    t, r = (cal or {}).get("threshold"), (cal or {}).get("recall_threshold")
    if r is not None:
        if t is not None and s >= t:
            return "yes"
        return "no" if s < r else ("yes" if calibrated(answer, spec, cal) >= 0.5 else "no")
    if t is None:
        return "yes" if calibrated(answer, spec, cal) >= 0.5 else "no"
    if cal.get("threshold_on") == "raw score":
        return "yes" if s >= t else "no"
    return "yes" if calibrated(answer, spec, cal) >= t else "no"


def fit(spec, answers, labels, precision_target=0.9, alpha=0.05, per_answer=False, recall_target=None):
    """answers: raw backend answers; labels: option names (choice) or booleans (noul), same order.

    Choice decisions also get `per_answer`: precision per answer among the accepted rows, with bounds and a flag for
    answers demonstrably below the target. With `per_answer=True` each answer gets its own threshold and guarantee
    (`mode: "per answer"`, `thresholds`); answers whose labelled rows can't certify the target get none.

    A choice label that is not one of this spec's options (an option another version has) can't be answered
    correctly: it is left out of the temperature fit but counts as wrong in the error and the threshold.

    Yes/no decisions can also take `recall_target`: rows scored below `recall_threshold` are answered "no" without
    review, while at least that share of the true yeses stays above it (#21).
    """
    if recall_target is not None and spec["kind"] != "noul":
        raise ValueError("recall targets apply to yes/no decisions")
    n = len(labels)
    if n < 30:
        raise ValueError(f"only {n} labeled rows with answers; need at least 30 (100–200 recommended)")
    common = {"n_labels": n, "precision_target": precision_target, "alpha": alpha,
              "fitted_at": datetime.now(timezone.utc).isoformat()}
    if spec["kind"] == "choice":
        names, display = option_names(spec), option_display(spec)
        P = np.array([choice_vector(a, display) for a in answers])
        known = np.array([l in names for l in labels])
        if known.sum() < 10:                                    # enough to fit one temperature
            raise ValueError(f"only {int(known.sum())} labels name an option of this version; need at least 10")
        y = np.array([names.index(l) if k else -1 for l, k in zip(labels, known)])
        floor = float(P[P > 0].min() / 2) if (P == 0).any() else 0.0
        Pf = np.maximum(P, floor)
        Pf = Pf / Pf.sum(axis=1, keepdims=True)
        T = M.temperature_fit(Pf[known], y[known])
        P_cv = np.empty_like(Pf)
        P_cv[known], _ = M.crossfit_temperature(Pf[known], y[known])
        if not known.all():
            P_cv[~known] = M.temperature_apply(Pf[~known], T)
        conf_cv, correct_cv = P_cv.max(axis=1), (P_cv.argmax(axis=1) == y).astype(float)
        raw_conf, raw_correct = P.max(axis=1), (P.argmax(axis=1) == y).astype(float)
        cal = {"method": "temperature", "temperature": T, "floor": floor,
               "ece_raw": M.ece(raw_conf, raw_correct, 15), "ece_calibrated": M.ece(conf_cv, correct_cv, 15),
               "accuracy": float(raw_correct.mean()), "labels_not_options": int((~known).sum())}
        ok = raw_correct
    else:
        p = np.array([float(a["noul"]) for a in answers])
        y = np.array([bool(l) for l in labels], float)
        a, b = M.platt_fit(p, y)
        p_cv = M.crossfit_binary(p, y, "platt")
        cal = {"method": "platt", "a": float(a), "b": float(b),
               "ece_raw": M.ece(p, y, 15), "ece_calibrated": M.ece(p_cv, y, 15), "prevalence": float(y.mean())}
        ok = y
    score = np.array([raw_score(a, spec) for a in answers])
    th = M.precision_threshold(score, ok, precision_target, alpha)
    chosen = th["chosen"]
    cal.update(common, threshold=chosen["threshold"] if chosen else None, threshold_on="raw score",
               coverage=chosen["coverage"] if chosen else 0.0,
               observed_precision=chosen["precision"] if chosen else None)
    if recall_target is not None:
        rt = M.recall_threshold(score, ok.astype(bool), recall_target, alpha)
        c = rt["chosen"]
        cal.update(recall_target=recall_target, recall_threshold=c["threshold"] if c else None,
                   recall_observed=c["recall"] if c else None, recall_lower=c["lower_bound"] if c else None,
                   dismissed=c["dismissed"] if c else 0.0, positives=rt["positives"],
                   positives_needed=rt["positives_needed"])
    if spec["kind"] == "choice":
        pred = np.array([names[i] for i in P.argmax(axis=1)])
        overall = M.per_answer(score, ok, pred, cal["threshold"], precision_target, alpha)
        own = {r["answer"]: r["own_threshold"] for r in overall}
        acc = np.array([own.get(a) is not None and s >= own[a] for a, s in zip(pred, score)])
        if per_answer:
            cal.update(mode="per answer", thresholds=own, threshold=None, coverage=float(acc.mean()),
                       observed_precision=float(ok[acc].mean()) if acc.any() else None,
                       per_answer=M.per_answer(score, ok, pred, own, precision_target, alpha))
        else:
            cal.update(per_answer=overall, per_answer_coverage=float(acc.mean()))    # what --per-answer would allow
    return cal
