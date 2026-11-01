"""Several models and labels in one estimate, and the cheapest mix of them, on real answers (#27, no API calls).

The 2,000 CFPB complaints carry the product the consumer chose (the labels) and three stored sets of answers: Jev 1.13
one row per request, Jev 1.13 at 20 rows per request (about half the tokens per row) and Laya (open weights, run here
on a CPU). Costs per row are measured: tokens at TypeSafe's list price for Jev; for Laya, seconds on this machine.

1. coverage of `estimate --with` on nested samples (labels inside a model's sample inside another model's rows) and on
   independent ones, against PPI++ with one model, on a population of 200,000 complaints resampled from the 2,000;
2. what the cheapest mix costs for ± 2 points, against judging every row and against labels alone, for tables of
   10,000 to 100 million rows and labels at $0.25 to $4 (the models' correlations with the labels, measured here);
3. whether a plan delivers: its labels and samples drawn on the resampled population, the interval it gets;
4. at what cost per row a second model is worth having. Laya's cost is the machine it runs on, not a price, so the
   dollar tables keep to the two ways of calling Jev, and Laya gets the cost below which it would pay for itself.
"""
import json
import math
import os

import numpy as np

from pgdocket import groups as G
from pgdocket import multi as M
from pgdocket.backend import USD_PER_INPUT_TOKEN
from pgdocket.estimate import norm_ppf

from .db import connect

OUT = "results/multi-validation"
RUNS = {"Jev, 1 row per request": "jev-20261019-073856", "Jev, 20 rows per request": "jev-20261017-122314",
        "Laya": "laya-20261030-103603"}
SHORT = {"Jev, 1 row per request": "jev", "Jev, 20 rows per request": "jev-20", "Laya": "laya"}
PRICED = ("Jev, 1 row per request", "Jev, 20 rows per request")          # models with a price per row
ANSWERS = ("credit reporting", "debt collection", "credit card", "mortgage")
TABLES = (10_000, 1_000_000, 100_000_000)
LABEL_COSTS = (0.25, 1.0, 4.0)
NPOP = 200_000
Z = norm_ppf(0.975)
NAN = np.nan


def load():
    """Truth, each predictor's answers and its measured cost per row."""
    with connect() as conn:
        rows = conn.execute("SELECT text_hash, product FROM bench.complaints ORDER BY complaint_id").fetchall()
        ans = {name: dict(conn.execute("SELECT text_hash, answer->>'choice' FROM bench.results "
                                       "WHERE run_id = %s AND experiment = 'choice_base'", (run,)).fetchall())
               for name, run in RUNS.items()}
    keep = [r for r in rows if all(r[0] in a for a in ans.values())]
    cost, seconds = {}, {}
    for name, run in RUNS.items():
        m = json.load(open(f"results/{run}/metrics.json"))["experiment_meta"]["choice_base"]
        st = m.get("stats", {})
        cost[name] = st.get("input_tokens", 0) * USD_PER_INPUT_TOKEN / len(keep) if name.startswith("Jev") else 0.0
        seconds[name] = m["seconds"] / len(keep)
    return {"truth": np.array([p for _, p in keep]), "models": {n: np.array([a[h] for h, _ in keep]) for n, a in ans.items()},
            "cost": cost, "seconds": seconds}


def _draw(rng, n0, m, N):
    order = rng.permutation(N)
    lab = np.zeros(N, bool)
    lab[order[:n0]] = True
    sample = np.zeros(N, bool)
    sample[order[:m]] = True
    return lab, sample


def coverage(Y, F, trials, rng, designs=((150, 3000), (400, 20000))):
    """How often each interval holds the population's share, among draws with 10 labels each way."""
    N, truth = len(Y), float(Y.mean())
    jev, cheap = F["Jev, 1 row per request"], F["Laya"]
    out = {}
    for n0, m in designs:
        res = {k: [] for k in ("ppi", "jev sample", "jev sample + laya everywhere", "independent samples")}
        for _ in range(trials):
            lab, sample = _draw(rng, n0, m, N)
            k = int(Y[lab].sum())
            if min(k, n0 - k) < 10:
                continue
            u = sample & ~lab
            p = G.ppi_binary(n0, k, int(jev[lab].sum()), int((jev * Y)[lab].sum()), int(u.sum()), int(jev[u].sum()))
            ind = np.zeros(N, bool)
            ind[rng.choice(N, m, replace=False)] = True
            for key, est in (
                    ("ppi", p),
                    ("jev sample", M.estimate(M.pattern_stats(["labels", "jev"], [np.where(lab, Y, NAN),
                                                                                  np.where(sample, jev, NAN)]), Z, N)),
                    ("jev sample + laya everywhere", M.estimate(M.pattern_stats(
                        ["labels", "jev", "laya"], [np.where(lab, Y, NAN), np.where(sample, jev, NAN), cheap]), Z, N)),
                    ("independent samples", M.estimate(M.pattern_stats(
                        ["labels", "jev"], [np.where(lab, Y, NAN), np.where(ind, jev, NAN)]), Z, N))):
                if est and est["theta"] is not None:
                    res[key].append((abs(est["theta"] - truth) <= Z * est["se"], Z * est["se"]))
        out[f"{n0} labels, {m:,} rows judged"] = {
            k: {"coverage": float(np.mean([h for h, _ in v])), "half_width": float(np.mean([w for _, w in v])),
                "draws": len(v)} for k, v in res.items() if v}
    return out


def plans(share, rho, cost, names):
    """The cheapest mix for ± 2 points, Jev (one row per request) on every row as pgdocket judges by default, and
    labels alone, per table size and label cost."""
    out = {}
    for N in TABLES:
        for c0 in LABEL_COSTS:
            ps = M.plan(share, [rho[n] for n in names], [cost[n] for n in names], c0, N, 0.02, Z,
                        [SHORT[n] for n in names])
            best = ps[0]
            every = next(p for p in ps if p["kind"] == "every row" and p["models"] == ["jev"])
            alone = next(p for p in ps if p["kind"] == "labels alone")
            out[f"{N}|{c0}"] = {"best": best, "every_row": every, "labels_alone": alone}
    return out


def delivered(Y, F, cost, share_rho, trials, rng):
    """The cheapest plan for this population (200,000 rows, labels at $1), carried out: its labels inside its
    samples, and the interval it gets."""
    names = [n for n in PRICED if share_rho[n] > 0]
    share = float(Y.mean())
    best = M.plan(share, [share_rho[n] for n in names], [cost[n] for n in names], 1.0, len(Y), 0.02, Z,
                  [SHORT[n] for n in names])[0]
    N, truth = len(Y), share
    hits, widths = [], []
    for _ in range(trials):
        order = rng.permutation(N)
        cols, srcs = [np.full(N, NAN)], ["labels"]
        cols[0][order[:best["labels"]]] = Y[order[:best["labels"]]]
        for n in names:
            m = best["rows"].get(SHORT[n])
            if not m:
                continue
            c = np.full(N, NAN)
            c[order[:m]] = F[n][order[:m]]
            cols.append(c)
            srcs.append(SHORT[n])
        e = M.estimate(M.pattern_stats(srcs, cols), Z, N)
        if e["theta"] is not None and e["reliable"]:
            hits.append(abs(e["theta"] - truth) <= Z * e["se"])
            widths.append(Z * e["se"])
    return {"plan": best, "coverage": float(np.mean(hits)), "half_width": float(np.mean(widths)),
            "within_target": float(np.mean(np.array(widths) <= 0.02 * 1.05)), "draws": len(hits)}


def worth(rho_best, rho_cheap, cost_best):
    """The cost per row below which a cheaper model, correlated rho_cheap with the labels, is worth adding to one
    correlated rho_best (multifidelity Monte Carlo's condition)."""
    if rho_cheap <= 0 or rho_cheap >= rho_best:
        return None
    return cost_best * rho_cheap ** 2 / (rho_best ** 2 - rho_cheap ** 2)


def run(trials=300, seed=0):
    data = load()
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(data["truth"]), NPOP)
    res = {"rows": int(len(data["truth"])), "cost": data["cost"], "seconds": data["seconds"], "trials": trials,
           "answers": {}}
    for ans in ANSWERS:
        y = (data["truth"] == ans).astype(float)
        F = {n: (a == ans).astype(float) for n, a in data["models"].items()}
        rho = {n: float(np.corrcoef(y, f)[0, 1]) for n, f in F.items()}
        Y, Fp = y[idx], {n: f[idx] for n, f in F.items()}
        names = sorted(PRICED, key=lambda n: -rho[n])
        res["answers"][ans] = {
            "share": float(y.mean()), "rho": rho,
            "agree": {f"{a}|{b}": float(np.corrcoef(F[a], F[b])[0, 1]) for a in F for b in F if a < b},
            "coverage": coverage(Y, Fp, trials, rng),
            "plans": plans(float(y.mean()), rho, data["cost"], names),
            "delivered": delivered(Y, Fp, data["cost"], rho, max(100, trials // 3), rng),
            "worth": {n: worth(rho["Jev, 1 row per request"], rho[n], data["cost"]["Jev, 1 row per request"])
                      for n in F if not n.startswith("Jev, 1")}}
    os.makedirs(OUT, exist_ok=True)
    with open(f"{OUT}/results.json", "w") as fh:
        json.dump(res, fh, indent=2, default=float)
    with open(f"{OUT}/report.md", "w") as fh:
        fh.write(render(res))
    return OUT


def render(res):
    pct = lambda x: f"{100 * x:.1f} %"
    usd = lambda x: f"${x:,.0f}" if x >= 100 else f"${x:,.2f}"
    L = ["# Several models and labels together, on real answers", "",
         f"No API calls. Generated by `jevbench multivalidate`. {res['rows']:,} CFPB complaints with the product the "
         "consumer chose and three stored sets of answers. Measured cost per row: " + "; ".join(
             f"{n} {('$' + format(res['cost'][n] * 1e6, '.1f') + ' per million rows') if res['cost'][n] else 'no API cost'}"
             f" ({res['seconds'][n] * 1000:.0f} ms of wall time per row as run)" for n in res["cost"]) + ". "
         f"Coverage: {res['trials']} draws per cell on {NPOP:,} complaints resampled from the {res['rows']:,}. "
         "Intervals at 95 %; only draws with at least 10 labels with and without the answer count.", ""]
    for ans, a in res["answers"].items():
        L += [f"## {ans.capitalize()}: {pct(a['share'])} of complaints", "",
              "Correlation with the labels: " + ", ".join(f"{n} {r:.2f}" for n, r in a["rho"].items()) + ". Between "
              "models: " + ", ".join(f"{k.replace('|', ' and ')} {v:.2f}" for k, v in a["agree"].items()) + ".", "",
              "**1. Coverage** (share of intervals holding the truth; mean half-width in points).", "",
              "| design | PPI++, Jev sample | Jev sample | Jev sample, Laya on every row | labels and Jev drawn apart |",
              "| --- | ---: | ---: | ---: | ---: |"]
        for d, c in a["coverage"].items():
            cell = lambda k: f"{pct(c[k]['coverage'])} ±{100 * c[k]['half_width']:.1f}" if k in c else "–"
            L.append(f"| {d} | {cell('ppi')} | {cell('jev sample')} | {cell('jev sample + laya everywhere')} | "
                     f"{cell('independent samples')} |")
        L += ["", "**2. Cost of ± 2 points** (the cheapest mix; Jev on every row; labels alone).", "",
              "| rows | label | cheapest mix | its cost | Jev on every row | labels alone |", "| ---: | ---: | --- | ---: | ---: | ---: |"]
        for key, p in a["plans"].items():
            N, c0 = key.split("|")
            b = p["best"]
            what = ("labels alone" if not b["models"] else f"{b['labels']:,} labels, " + ", ".join(
                f"{m} on {b['rows'][m]:,}" for m in b["models"]))
            L.append(f"| {int(N):,} | ${float(c0):.2f} | {what} | {usd(b['cost'])} | {usd(p['every_row']['cost'])} | "
                     f"{usd(p['labels_alone']['cost'])} |")
        dl = a["delivered"]
        b = dl["plan"]
        L += ["", f"**3. A plan carried out** ({NPOP:,} rows, labels at $1): {b['labels']:,} labels, " + ", ".join(
            f"{m} on {b['rows'][m]:,} rows" for m in b["models"]) + f". Intervals held {pct(dl['coverage'])}, half-width "
            f"{100 * dl['half_width']:.2f} points on average (target 2), within 5 % of the target in "
            f"{pct(dl['within_target'])} of {dl['draws']} draws.", "",
            "**4. Worth adding to Jev** (one row per request) only below this cost: " + ", ".join(
                f"{n} {('$' + format(w * 1e6, '.1f') + ' per million rows') if w is not None else 'never'}"
                for n, w in a["worth"].items()) + f". Laya took {res['seconds']['Laya'] * 1e6 / 3600:,.0f} hours of this "
            "machine per million rows.", ""]
    return "\n".join(L) + "\n"
