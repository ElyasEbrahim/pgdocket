"""Jev against Laya on the same labelled rows, from stored reports (#4, no API calls).

Two sources, both judged twice with only the backend changed: TypeSafe's hosted Jev (jev-1.13.0) and Laya
(Convai Innovations, open weights, run locally on CPU).
- `pgdocket check` reports: the same 500-row sample (same seed), question, options and labels, on three tasks;
- the Phase 0 runs (`jevbench run`): the six experiments on the 2,000 CFPB complaints, through pg-jev.
"""
import glob
import json
import os

OUT = "results/backends"
PAIRS = {
    "Kubernetes: which SIG? (10 options)": ("check-k8s-sig", "check-laya-k8s-sig"),
    "Kubernetes: is it a feature request? (yes/no)": ("check-k8s-feature", "check-laya-k8s-feature"),
    "CFPB: which product? (9 options)": ("check-cfpb-product", "check-laya-cfpb-product"),
}
# Phase 0: Jev's six experiments ran 20 rows per request (pg-jev's default); its one-row run covers choice_base only.
JEV_RUN, JEV_SINGLE_RUN = "jev-20261017-122314", "jev-20261019-073856"


def summary(folder):
    r = json.load(open(f"results/{folder}/report.json"))
    a, c, cfg = r["analysis"], r["cost"], r["config"]
    ladder = {round(x["target"], 2): x["coverage"] for x in a.get("ladder", [])}
    paid = c.get("paid", "typesafe.ai" in r["backend"]["url"])
    out = {"model": r["backend"]["model"], "rows": r["sample"]["labeled_rows"], "kind": cfg["kind"],
           "ece_raw": a["ece_raw"], "ece_calibrated": a["ece_calibrated"],
           "auto_90": ladder.get(0.9), "auto_95": ladder.get(0.95), "auto_80": ladder.get(0.8),
           "seconds_per_row": c["seconds"] / r["sample"]["labeled_rows"], "usd": c["usd"] if paid else 0.0,
           "usd_per_million": c["projected_usd_per_million_rows"] if paid else 0.0,
           "stability": (r.get("stability") or {}).get("changed"), "batching": r.get("batching"),
           "steering": r.get("steering")}
    if cfg["kind"] == "choice":
        out |= {"accuracy": a["accuracy"], "accuracy_ci": a["accuracy_ci"], "macro_f1": a["macro_f1"],
                "per_answer_coverage": a.get("per_answer_coverage")}
    else:
        out |= {"auroc": a["auroc"], "precision_05": a["precision_at_0.5"], "recall_05": a["recall_at_0.5"],
                "prevalence": a["prevalence"]}
    return out


def phase0(run_id):
    """The numbers the Phase 0 report leads with, from a run's metrics.json."""
    m = json.load(open(f"results/{run_id}/metrics.json"))
    e, o, d = m["experiments"], m.get("order_bias") or {}, m.get("label_set_drift") or {}
    out = {"run": run_id, "model": m["run"]["model"], "n": m["run"]["n_items"],
           "seconds": sum(x["seconds"] for x in m["experiment_meta"].values()),
           "rows_judged": sum((x["stats"] or {}).get("rows_evaluated", 0) for x in m["experiment_meta"].values()),
           "experiments": sorted(e), "order_flip": o.get("flip_rate"), "order_flip_ci": o.get("flip_rate_ci"),
           "first_lift": o.get("first_position_prob_lift"), "drift_flip": d.get("flip_rate"),
           "drift_to_other": d.get("share_to_other")}
    det = f"results/{run_id}/determinism_choice_base.json"
    if os.path.exists(det):                           # identical repeats: the noise floor under the order effect
        d = json.load(open(det))
        out["repeat_flip"], out["repeat_runs"] = 1 - d["rows_identical_top1_all_runs"], len(d["runs"])
    if "choice_base" in e:
        b = e["choice_base"]
        out |= {"accuracy": b["accuracy"], "accuracy_ci": b["accuracy_ci"], "macro_f1": b["macro_f1"],
                "ece": b["ece"], "ece_temperature": b["ece_after_temperature"], "ece_isotonic": b["ece_after_isotonic"],
                "temperature": b["temperature"]}
    for name in ("noul_debt_collection", "noul_incorrect_report"):
        if name in e:
            out[name] = {k: e[name][k] for k in ("auroc", "ece", "ece_after_isotonic", "ece_after_platt", "prevalence")}
    return out


def laya_run():
    runs = sorted(p.split("/")[-2] for p in glob.glob("results/laya-*/metrics.json"))
    return runs[-1] if runs else None


def run():
    res = {"check": {title: {"jev": summary(j), "laya": summary(l)} for title, (j, l) in PAIRS.items()
                     if os.path.exists(f"results/{j}/report.json") and os.path.exists(f"results/{l}/report.json")}}
    if laya_run():
        res["phase0"] = {"jev": phase0(JEV_RUN), "jev_single": phase0(JEV_SINGLE_RUN), "laya": phase0(laya_run())}
    os.makedirs(OUT, exist_ok=True)
    with open(f"{OUT}/results.json", "w") as f:
        json.dump(res, f, indent=2, default=float)
    with open(f"{OUT}/report.md", "w") as f:
        f.write(render(res))
    return OUT


def render(res):
    pct = lambda x: "–" if x is None else f"{100 * x:.1f} %"
    num = lambda x: "–" if x is None else f"{x:.3f}"
    pts = lambda x: "–" if x is None else f"{100 * x:+.1f} points".replace("-", "−")
    runs = lambda r: f"{pct(r.get('repeat_flip'))} ({r['repeat_runs']} runs)" if r.get("repeat_runs") else "–"
    L = ["# Jev and Laya on the same labelled rows", "",
         "No API calls. Generated by `jevbench backends` from stored reports; only the backend differs. Jev is "
         "TypeSafe's hosted model; Laya (Convai Innovations, open weights) ran on this machine's CPU in Docker.", "",
         "## `pgdocket check`, three tasks", "",
         "The same 500-row sample, question, options and labels. *Auto* is the share of rows `calibrate` could accept "
         "with a precision guarantee at 95 % confidence (raw-score threshold, as the view applies it).", ""]
    for title, b in res["check"].items():
        j, l = b["jev"], b["laya"]
        L += [f"### {title}", "", f"| | {j['model']} (TypeSafe, hosted) | {l['model']} (Laya, local CPU) |", "| --- | ---: | ---: |"]
        if j["kind"] == "choice":
            L += [f"| accuracy | {pct(j['accuracy'])} [{pct(j['accuracy_ci'][0])}, {pct(j['accuracy_ci'][1])}] | "
                  f"{pct(l['accuracy'])} [{pct(l['accuracy_ci'][0])}, {pct(l['accuracy_ci'][1])}] |",
                  f"| macro-F1 | {j['macro_f1']:.3f} | {l['macro_f1']:.3f} |"]
        else:
            L += [f"| ranking quality (AUROC) | {j['auroc']:.3f} | {l['auroc']:.3f} |",
                  f"| precision / recall at 0.5 | {pct(j['precision_05'])} / {pct(j['recall_05'])} | "
                  f"{pct(l['precision_05'])} / {pct(l['recall_05'])} |"]
        L += [f"| calibration error, raw → calibrated | {j['ece_raw']:.3f} → {j['ece_calibrated']:.3f} | "
              f"{l['ece_raw']:.3f} → {l['ece_calibrated']:.3f} |",
              f"| auto at ≥ 80 % / 90 % / 95 % precision | {pct(j['auto_80'])} / {pct(j['auto_90'])} / {pct(j['auto_95'])} | "
              f"{pct(l['auto_80'])} / {pct(l['auto_90'])} / {pct(l['auto_95'])} |"]
        if j["kind"] == "choice":
            L.append(f"| auto with every answer certified on its own (90 %) | {pct(j['per_answer_coverage'])} | "
                     f"{pct(l['per_answer_coverage'])} |")
        if j["stability"] is not None or l["stability"] is not None:
            L.append(f"| answers that change when asked again | {pct(j['stability'])} | {pct(l['stability'])} |")
        if j["batching"] and l["batching"]:
            jb, lb = j["batching"], l["batching"]
            L.append(f"| answers that change at 20 rows per request | {pct(jb['changed'])} | {pct(lb['changed'])} |")
            if "accuracy_batched" in jb:
                L.append(f"| accuracy, 1 → 20 rows per request (first 100 rows) | {pct(jb['accuracy_single'])} → "
                         f"{pct(jb['accuracy_batched'])} | {pct(lb['accuracy_single'])} → {pct(lb['accuracy_batched'])} |")
        L += [f"| wall time of the check, per labelled row | {j['seconds_per_row']:.3f} s | {l['seconds_per_row']:.3f} s |",
              f"| cost per million rows | ${j['usd_per_million']:.0f} | no API cost (own hardware) |", ""]
    p = res.get("phase0")
    if p:
        j, s, l = p["jev"], p["jev_single"], p["laya"]
        L += ["## Phase 0: six experiments on 2,000 CFPB complaints, through pg-jev", "",
              f"`jevbench run` with the same six experiments; runs `{j['run']}` and `{s['run']}` (Jev) and `{l['run']}` "
              "(Laya). Laya ran one row per request, because 20 per request breaks it (above). Jev's six-experiment run "
              "used 20 per request; its one-row run covers `choice_base` only.", "",
              f"| | {j['model']}, 20 rows per request | {s['model']}, 1 row per request | {l['model']}, 1 row per request |",
              "| --- | ---: | ---: | ---: |",
              f"| product accuracy | {pct(j['accuracy'])} | {pct(s['accuracy'])} | {pct(l['accuracy'])} |",
              f"| macro-F1 | {num(j['macro_f1'])} | {num(s['macro_f1'])} | {num(l['macro_f1'])} |",
              f"| calibration error (ECE), raw | {num(j['ece'])} | {num(s['ece'])} | {num(l['ece'])} |",
              f"| ECE after temperature scaling (T) | {num(j['ece_temperature'])} ({j['temperature']:.2f}) | "
              f"{num(s['ece_temperature'])} ({s['temperature']:.2f}) | {num(l['ece_temperature'])} ({l['temperature']:.2f}) |",
              f"| ECE after isotonic | {num(j['ece_isotonic'])} | {num(s['ece_isotonic'])} | {num(l['ece_isotonic'])} |",
              f"| answers that change when the run is repeated (noise) | {runs(j)} | – | {runs(l)} |",
              f"| answers that change across 3 option orders | {pct(j['order_flip'])} | – | {pct(l['order_flip'])} |",
              f"| probability lift for the option listed first | {pts(j['first_lift'])} | – | {pts(l['first_lift'])} |",
              f"| answers that change when a catch-all option is added | {pct(j['drift_flip'])} | – | {pct(l['drift_flip'])} |"]
        for name, label in (("noul_debt_collection", "debt collection?"), ("noul_incorrect_report", "incorrect credit report?")):
            if name in j and name in l:
                L += [f"| yes/no, {label} AUROC | {num(j[name]['auroc'])} | – | {num(l[name]['auroc'])} |",
                      f"| yes/no, {label} ECE raw → isotonic | {num(j[name]['ece'])} → {num(j[name]['ece_after_isotonic'])} | – | "
                      f"{num(l[name]['ece'])} → {num(l[name]['ece_after_isotonic'])} |"]
        per = lambda r: f"{r['seconds'] / r['rows_judged']:.3f} s" if r.get("rows_judged") else "–"
        L += [f"| wall time per row judged | {per(j)} | {per(s)} | {per(l)} |", ""]
    return "\n".join(L)
