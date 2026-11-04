"""Several models and human labels in one estimate, and the cheapest mix of them for a target (#27).

A share can come from human labels alone, or from labels and one model's answers on every row (PPI++). With several
models of different cost and accuracy, and labels dearer than any of them, the question is how many rows each should
judge. Multiple-prediction-powered inference (MultiPPI; Cowen-Breen et al., 2026) poses it; this module answers it for
pgdocket's case, where a version of a decision is a model and its answers are already in the ledger.

**The estimate.** Rows fall into patterns by the sources they have: a label drawn at random, and each model's answer
(the active version's, and those of the versions named with --with). When each source's rows are a random sample of
the scope, every pattern's means are unbiased for its sources' means, and the best linear unbiased estimate of all the
means at once is generalised least squares across patterns, MultiPPI's estimator for a given allocation:
    μ̂ = M⁻¹ Σ_j n_j P_jᵀ Σ_j⁻¹ x̄_j,  M = Σ_j n_j P_jᵀ Σ_j⁻¹ P_j,  Var(μ̂) = M⁻¹,
where P_j picks pattern j's sources and Σ_j their covariance. The share is the label's coordinate. Covariances come from
the rows that have both sources, so models' agreement is measured on every row they both judged. With one model on
every row this is the regression (difference) estimator that PPI++ approximates.

**The plan.** When samples are nested (labels inside the rows the best model judged, inside those the next one
judged, and so on), multifidelity Monte Carlo (Peherstorfer, Willcox & Gunzburger, 2016) gives the cheapest sample
sizes in closed form from each model's correlation ρ with the labels and its cost w per row: models are ordered by ρ,
a model is worth its cost only if w_prev / w > (ρ_prev² − ρ²) / (ρ² − ρ_next²), and level i gets m_i ∝
√(w_0 (ρ_i² − ρ_{i+1}²) / (w_i (1 − ρ_1²))) rows. A table has only so many rows: a level that would judge more judges
every row, and a model free per row judges every row.
"""
import itertools
import math

import numpy as np
from psycopg import sql

from .estimate import MIN_EACH_SIDE

LABEL = 0             # source index of the human label; models are 1..K


def covariance(stats):
    """Covariance of the sources (label, then each model). The models' block comes from every row that has both
    models; the label's relation to them is a regression on the labelled rows that have every model, with half a
    disagreement added each way (a Jeffreys prior, as in `groups.sps_estimate`): Σ_ff, Σ_yf = β Σ_ff, Σ_yy = σ²_y|f +
    β Σ_ff βᵀ. Covariances estimated from different rows could imply a correlation above 1, and a model that agreed
    with every labelled row would leave no error at all; this way neither happens."""
    k = len(stats["sources"])
    models = [i for i in range(k) if i != LABEL]

    def pooled(pats, a, b):                                  # (rows, Σa, Σb, Σab) over the patterns with both
        n = sa = sb = sab = 0.0
        for p in pats:
            if a in p["mask"] and b in p["mask"]:
                n += p["n"]
                sa += p["sum"][p["mask"].index(a)]
                sb += p["sum"][p["mask"].index(b)]
                sab += p["sq"][a] if a == b else p["prod"][(min(a, b), max(a, b))]
        return n, sa, sb, sab

    ff = np.zeros((len(models), len(models)))
    for i, a in enumerate(models):
        for j, b in enumerate(models[i:], i):
            n, sa, sb, sab = pooled(stats["patterns"], a, b)
            ff[i, j] = ff[j, i] = (sab - sa * sb / n) / (n - 1) if n > 1 else 0.0
    if models:
        w, v = np.linalg.eigh(ff)
        ff = (v * np.maximum(w, 1e-6 * max(1e-12, float(np.trace(ff))))) @ v.T
    full = [p for p in stats["patterns"] if len(p["mask"]) == k]   # labelled, and judged by every model
    S = np.zeros((k, k))
    for a in range(k):
        for b in range(a, k):
            n, sa, sb, sab = pooled(full, a, b)
            # two pseudo-rows of weight ½, (label 1, every model 0) and (label 0, every model 1): each source sums ½
            # more, its products with itself ½, the label's with a model 0 and two models' ½
            pab = 0.5 if a == b else (0.0 if LABEL in (a, b) else 0.5)
            n, sa, sb, sab = n + 1, sa + 0.5, sb + 0.5, sab + pab
            S[a, b] = S[b, a] = (sab - sa * sb / n) / (n - 1) if n > 1 else 0.0
    cov = np.zeros((k, k))
    if not models:
        cov[0, 0] = S[0, 0]
        return cov
    beta = np.linalg.lstsq(S[np.ix_(models, models)], S[models, LABEL], rcond=None)[0]   # models may agree throughout
    resid = max(S[LABEL, LABEL] - float(beta @ S[models, LABEL]), 1e-12)
    cov[np.ix_(models, models)] = ff
    cov[LABEL, models] = cov[models, LABEL] = beta @ ff
    cov[LABEL, LABEL] = resid + float(beta @ ff @ beta)
    return cov


def gls(stats, cov=None):
    """MultiPPI's estimate of every source's mean from the patterns' means: (means, their covariance)."""
    k = len(stats["sources"])
    cov = covariance(stats) if cov is None else cov
    M, b = np.zeros((k, k)), np.zeros(k)
    for p in stats["patterns"]:
        idx = list(p["mask"])
        inv = np.linalg.inv(cov[np.ix_(idx, idx)])
        M[np.ix_(idx, idx)] += p["n"] * inv
        b[idx] += inv @ np.asarray(p["sum"], float)          # n · Σ⁻¹ · mean = Σ⁻¹ · sum
    Minv = np.linalg.inv(M)
    return Minv @ b, Minv


def estimate(stats, z, rows):
    """The share of the label's answer in the scope (`rows` rows) from every source, with its interval. None without
    labels with and without the answer; exact when every row is labelled. Also how many rows each source covers."""
    labelled = sum(p["n"] for p in stats["patterns"] if LABEL in p["mask"])
    yes = sum(p["sum"][p["mask"].index(LABEL)] for p in stats["patterns"] if LABEL in p["mask"])
    out = {"labels": labelled, "labels_with": yes,
           "rows": {s: sum(p["n"] for p in stats["patterns"] if i in p["mask"]) for i, s in enumerate(stats["sources"])}}
    if labelled and labelled >= rows:
        return {**out, "theta": yes / labelled, "se": 0.0, "exact": True, "reliable": True, "ci": (yes / labelled,) * 2}
    if labelled < 2 or yes in (0, labelled):
        return {**out, "theta": None, "se": None, "exact": False, "reliable": False, "ci": None}
    keep = [i for i in range(len(stats["sources"])) if i == LABEL or _var(stats, i) > 0]   # a constant model says nothing
    for i in list(keep[1:]):                                  # nor does one that repeats another, wherever both judged
        if any(j < i and _same(stats, i, j) for j in keep[1:]):
            keep.remove(i)
    while len(keep) > 1:
        # a model counts only while the labelled rows that every model kept judged hold MIN_EACH_SIDE with and without
        # the answer: its relation to the label would otherwise rest on a handful of rows
        full = [p for p in _restrict(stats, keep)["patterns"] if len(p["mask"]) == len(keep)]
        n, k = sum(p["n"] for p in full), sum(p["sum"][0] for p in full)
        if min(k, n - k) >= MIN_EACH_SIDE:
            break
        keep.remove(min(keep[1:], key=lambda i: sum(p["n"] for p in stats["patterns"]
                                                    if LABEL in p["mask"] and i in p["mask"])))
    st = _restrict(stats, keep)
    mu, cov = gls(st)
    se = math.sqrt(max(0.0, cov[0, 0]))
    theta = float(mu[0])
    return {**out, "theta": theta, "se": se, "exact": False,
            "reliable": min(yes, labelled - yes) >= MIN_EACH_SIDE,
            "ci": (max(0.0, theta - z * se), min(1.0, theta + z * se)), "used": [stats["sources"][i] for i in keep]}


def _same(stats, a, b):
    """Whether models a and b gave the same answer on every row both judged (0/1 answers: Σ(a − b)² = 0)."""
    pats = [p for p in stats["patterns"] if a in p["mask"] and b in p["mask"]]
    return bool(pats) and sum(p["sq"][a] + p["sq"][b] - 2 * p["prod"][(min(a, b), max(a, b))] for p in pats) == 0


def _var(stats, i):
    n = sum(p["n"] for p in stats["patterns"] if i in p["mask"])
    s = sum(p["sum"][p["mask"].index(i)] for p in stats["patterns"] if i in p["mask"])
    sq = sum(p["sq"][i] for p in stats["patterns"] if i in p["mask"])
    return 0.0 if n < 2 else (sq - s * s / n) / (n - 1)


def _restrict(stats, keep):
    """The statistics with only the sources in `keep` (renumbered in that order); patterns that lose every source
    go."""
    pos = {s: j for j, s in enumerate(keep)}
    pats = {}
    for p in stats["patterns"]:
        mask = tuple(pos[s] for s in p["mask"] if s in pos)
        if not mask:
            continue
        q = pats.setdefault(mask, {"mask": mask, "n": 0, "sum": [0.0] * len(mask),
                                    "sq": {m: 0.0 for m in mask}, "prod": {}})
        q["n"] += p["n"]
        for s in p["mask"]:
            if s in pos:
                q["sum"][mask.index(pos[s])] += p["sum"][p["mask"].index(s)]
                q["sq"][pos[s]] += p["sq"][s]
        for (a, b), v in p["prod"].items():
            if a in pos and b in pos:
                key = (min(pos[a], pos[b]), max(pos[a], pos[b]))
                q["prod"][key] = q["prod"].get(key, 0.0) + v
    return {"sources": [stats["sources"][s] for s in keep], "patterns": list(pats.values())}


def pattern_stats(sources, columns):
    """The statistics `estimate` takes, from arrays (one per source, NaN where a row lacks it). For the harness and
    tests; the ledger builds the same sums in SQL."""
    X = np.column_stack([np.asarray(c, float) for c in columns])
    seen = ~np.isnan(X)
    code = (seen * (1 << np.arange(X.shape[1]))).sum(axis=1)  # which sources each row has, as bits
    pats = []
    for c in sorted(set(np.unique(code).tolist()), reverse=True):
        rows = code == c
        mask = tuple(i for i in range(X.shape[1]) if c >> i & 1)
        if not mask:
            continue
        Xp = X[rows][:, mask]
        pats.append({"mask": mask, "n": int(rows.sum()), "sum": [float(v) for v in Xp.sum(axis=0)],
                     "sq": {s: float((Xp[:, j] ** 2).sum()) for j, s in enumerate(mask)},
                     "prod": {(mask[a], mask[b]): float((Xp[:, a] * Xp[:, b]).sum())
                              for a in range(len(mask)) for b in range(a + 1, len(mask))}})
    return {"sources": list(sources), "patterns": pats}


# ---------------------------------------------------------------- the cheapest mix for a target
def plan(share, rho, costs, label_cost, rows, target, z, names=None, labelled=0, judged=None):
    """The cheapest nested sample sizes for an interval of ± `target` on a share near `share`, among: labels alone;
    labels with each subset of the models (multifidelity Monte Carlo, capped at the table's `rows`); and labels with
    each model on every row, as pgdocket judges by default. `rho`: each model's correlation with the labels; `costs`:
    each model's cost per row. Work already done is paid for: `labelled` labels, `judged` rows per model (a model that
    judged every row costs nothing more). Returns the plans, cheapest first, each with its labels, rows per model and
    the cost of what it still needs."""
    names = names or [f"model {i + 1}" for i in range(len(rho))]
    judged = judged or [0] * len(rho)
    costs = [0.0 if j >= rows else c for c, j in zip(costs, judged)]
    var_y = share * (1 - share)
    V = (target / z) ** 2
    floor = MIN_EACH_SIDE / max(min(share, 1 - share), 1e-9)
    plans = [_nested([], [], [], var_y, label_cost, rows, V, floor, "labels alone")]
    done = dict(zip(names, judged))
    for size in range(1, len(rho) + 1):
        for subset in itertools.combinations(range(len(rho)), size):
            ordered = sorted(subset, key=lambda i: -abs(rho[i]))
            p = _nested([names[i] for i in ordered], [abs(rho[i]) for i in ordered], [costs[i] for i in ordered],
                        var_y, label_cost, rows, V, floor, None)
            if p:
                plans.append(p)
    for i in range(len(rho)):                                # judging every row, as pgdocket does by default
        p = _nested([names[i]], [abs(rho[i])], [costs[i]], var_y, label_cost, rows, V, floor, "every row",
                    every_row=True)
        if p:
            plans.append(p)
    for p in plans:                                         # what each plan still costs
        p["more_labels"] = max(0, p["labels"] - labelled)
        p["more_rows"] = {m: max(0, r - done.get(m, 0)) for m, r in p["rows"].items()}
        p["cost"] = label_cost * p["more_labels"] + sum(costs[names.index(m)] * r for m, r in p["more_rows"].items())
    return sorted(plans, key=lambda p: (p["cost"], p["labels"]))


def _nested(names, rho, w, var_y, w0, N, V, floor, kind, every_row=False):
    """One nested plan: labels m_0 inside m_1 rows judged by the best model, inside m_2 by the next… None when a model
    isn't worth its cost (the multifidelity conditions) or no sample size reaches the target."""
    r = list(rho) + [0.0]
    if any(r[i] <= r[i + 1] for i in range(len(rho))) or (rho and rho[0] >= 1):
        return None                                          # correlations must fall strictly from model to model
    ws = [w0] + list(w)
    rr = [1.0] + list(r)
    if not every_row:
        for i in range(1, len(ws)):                          # each model must be cheap enough for what it adds
            if ws[i] > 0 and not ws[i - 1] / ws[i] > (rr[i - 1] ** 2 - rr[i] ** 2) / (rr[i] ** 2 - rr[i + 1] ** 2):
                return None
    ratios = []
    for i in range(1, len(ws)):                               # m_i / m_0 (∞ for a model free per row)
        if every_row or ws[i] == 0:
            ratios.append(math.inf)
        else:
            ratios.append(math.sqrt(w0 * (rr[i] ** 2 - rr[i + 1] ** 2) / (ws[i] * (1 - rr[1] ** 2))))

    def sizes(m0):
        m, prev = [], m0
        for q in ratios:
            prev = min(N, max(prev, q * m0))
            m.append(prev)
        return m

    def var(m0):
        m, total, prev = sizes(m0), 1 / m0, m0
        for i, mi in enumerate(m):
            total -= (1 / prev - 1 / mi) * rr[i + 1] ** 2
            prev = mi
        return var_y * total

    if var(N) > V:                                           # even labelling every row wouldn't do: it is exact then
        m0 = N
    else:
        lo, hi = 1.0, float(N)
        for _ in range(100):
            mid = (lo + hi) / 2
            lo, hi = (mid, hi) if var(mid) > V else (lo, mid)
        m0 = hi
    m0 = min(N, max(m0, floor))
    m = sizes(m0)
    cost = w0 * m0 + sum(wi * mi for wi, mi in zip(w, m))
    return {"kind": kind or ("models on samples" if names else "labels alone"), "labels": math.ceil(m0),
            "rows": {n: math.ceil(mi) for n, mi in zip(names, m)}, "cost": cost, "models": list(names)}


# ---------------------------------------------------------------- database
def _order(d, seed):
    """The shared order every sample here takes a prefix of: rows labelled at random first, then the rest by
    md5(key || ':j:' || seed). A version's sample and the next labels are prefixes of it, so they nest."""
    return sql.SQL("(l.row_key IS NOT NULL AND l.sampling = 'random') DESC, md5(s.{k}::text || ':j:' || %(seed)s)").format(
        k=sql.Identifier(d["key"]))


def _counts(by):
    """When a label is a sample for this estimate: drawn at random, or, by groups, drawn within this column's groups
    (#25). Parameter: by."""
    return sql.SQL("l.sampling = 'random' OR (l.sampling = 'stratified' AND %(by)s = ANY(l.strata))" if by
                   else "l.sampling = 'random'")


def _group(by):
    from .groups import ALL
    return sql.SQL("s.{}::text").format(sql.Identifier(by)) if by else sql.SQL("{}::text").format(sql.Literal(ALL))


def ledger_stats(conn, d, versions, answer, where=None, by=None):
    """One pass over the scope: per pattern of sources (a label that counts as a sample; each version's current
    answer), the counts and sums `estimate` takes. Returns (stats, rows in scope); with `by`, a list of
    (group, stats, rows) for each value of that source column, in order."""
    from .groups import _cond
    noul = d["spec"]["kind"] == "noul"
    cols = [_group(by) + sql.SQL(" AS g"),
            sql.SQL("CASE WHEN {} THEN (l.label = %(ans)s)::int END AS s0").format(_counts(by))]
    joins = []
    for i, v in enumerate(versions, 1):
        c, j = sql.Identifier(f"c{i}"), sql.Identifier(f"j{i}")
        joins.append(sql.SQL(" LEFT JOIN docket.current {c} ON {c}.decision_id = %(d)s AND {c}.version = {v} "
                             "AND {c}.row_key = s.{k}::text LEFT JOIN docket.judgment {j} ON {j}.id = {c}.judgment_id")
                     .format(c=c, j=j, v=sql.Literal(int(v)), k=sql.Identifier(d["key"])))
        # a yes/no model says yes at 0.5 here: versions calibrated differently stay comparable
        cols.append(sql.SQL("({j}.score >= 0.5)::int AS {s}" if noul else "({j}.value = %(ans)s)::int AS {s}")
                    .format(j=j, s=sql.Identifier(f"s{i}")))
    k = len(versions) + 1
    code = sql.SQL(" + ").join(sql.SQL("({} IS NOT NULL)::int * {}").format(sql.Identifier(f"s{i}"), sql.Literal(1 << i))
                               for i in range(k))
    sums = [sql.SQL("coalesce(sum({}), 0)").format(sql.Identifier(f"s{i}")) for i in range(k)]
    pairs = [(a, b) for a in range(k) for b in range(a + 1, k)]
    prods = [sql.SQL("coalesce(sum({} * {}), 0)").format(sql.Identifier(f"s{a}"), sql.Identifier(f"s{b}")) for a, b in pairs]
    q = sql.SQL("""
        WITH src AS (
            SELECT {cols} FROM {table} s
            LEFT JOIN docket.label l ON l.decision_id = %(d)s AND l.row_key = s.{k}::text{joins}
            WHERE {cond}
        )
        SELECT g, {code} AS code, count(*), {sums}{prods} FROM src GROUP BY 1, 2 ORDER BY 1 NULLS LAST""").format(
        cols=sql.SQL(", ").join(cols), table=sql.SQL(d["source"]), k=sql.Identifier(d["key"]),
        joins=sql.SQL("").join(joins), cond=_cond(where), code=code, sums=sql.SQL(", ").join(sums),
        prods=sql.SQL("").join(sql.SQL(", ") + p for p in prods))
    sources = ["labels"] + [f"v{v}" for v in versions]
    out = {}
    for r in conn.execute(q, {"ans": answer, "d": d["id"], "by": by}).fetchall():
        g, c, n = r[0], int(r[1]), int(r[2])
        st, rows = out.setdefault(g, ({"sources": sources, "patterns": []}, [0]))
        rows[0] += n
        mask = tuple(i for i in range(k) if c >> i & 1)
        if not mask:
            continue
        sm = [float(r[3 + i]) for i in range(k)]
        pr = dict(zip(pairs, (float(x) for x in r[3 + k:])))
        st["patterns"].append({"mask": mask, "n": n, "sum": [sm[i] for i in mask], "sq": {i: sm[i] for i in mask},
                               "prod": {p: v for p, v in pr.items() if p[0] in mask and p[1] in mask}})
    groups = [(g, st, rows[0]) for g, (st, rows) in out.items()]
    if by:
        return groups
    return (groups[0][1], groups[0][2]) if groups else ({"sources": sources, "patterns": []}, 0)


def queue_samples(conn, d, want, by=None, where=None, seed="1"):
    """Queue what a plan asks each version to judge: for (group, version, m) in `want`, the first m rows of the group's
    shared order (rows whose label counts first, then md5(key || ':j:' || seed)) that the version hasn't judged.
    Samples of different versions and the labels nest. Returns rows queued."""
    from .groups import _cond
    total = 0
    for v in sorted({w[1] for w in want}):
        mine = [(g, m) for g, ver, m in want if ver == v and m > 0]
        if not mine:
            continue
        q = sql.SQL("""
            WITH want AS (SELECT * FROM unnest(%(g)s::text[], %(m)s::int[]) AS x(g, m)),
            cand AS (
                SELECT s.{k}::text AS k, {grp} AS g,
                       row_number() OVER (PARTITION BY {grp} ORDER BY coalesce({counts}, false) DESC,
                                          md5(s.{k}::text || ':j:' || %(seed)s)) AS rn
                FROM {table} s LEFT JOIN docket.label l ON l.decision_id = %(d)s AND l.row_key = s.{k}::text
                WHERE {cond}
            )
            INSERT INTO docket.queue (decision_id, version, row_key)
            SELECT %(d)s, %(v)s, c.k FROM cand c JOIN want x ON x.g IS NOT DISTINCT FROM c.g
            WHERE c.rn <= x.m AND NOT EXISTS (SELECT 1 FROM docket.current cur WHERE cur.decision_id = %(d)s
                                              AND cur.version = %(v)s AND cur.row_key = c.k)
            ON CONFLICT DO NOTHING""").format(
            k=sql.Identifier(d["key"]), grp=_group(by), counts=_counts(by), table=sql.SQL(d["source"]),
            cond=_cond(where))
        total += conn.execute(q, {"g": [g for g, _ in mine], "m": [int(m) for _, m in mine], "d": d["id"],
                                  "v": int(v), "seed": str(seed), "by": by}).rowcount
    conn.execute("SELECT pg_notify('docket', %s)", (str(d["id"]),))
    return total


def version_info(conn, d, wanted, costs=None):
    """The active version and the ones asked for: number, model, backend, rows judged and cost per row (mean tokens of
    its judgments at its backend's price; `costs` {version: USD} overrides, e.g. for hardware a free backend runs
    on)."""
    from .backend import price_per_token
    active = conn.execute("SELECT active_version FROM docket.decision WHERE id = %s", (d["id"],)).fetchone()[0]
    out = []
    for v in [active] + [w for w in wanted if w != active]:
        r = conn.execute("SELECT spec, status FROM docket.version WHERE decision_id = %s AND version = %s",
                         (d["id"], v)).fetchone()
        if not r:
            raise SystemExit(f"{d['name']} has no version {v}")
        judged, tokens = conn.execute(
            "SELECT count(*), avg(j.tokens) FROM docket.current c JOIN docket.judgment j ON j.id = c.judgment_id "
            "WHERE c.decision_id = %s AND c.version = %s", (d["id"], v)).fetchone()
        cost = (costs or {}).get(v)
        out.append({"version": v, "model": r[0].get("model"), "backend": r[0].get("backend"), "status": r[1],
                    "judged": int(judged), "cost": float(cost) if cost is not None else
                    float(tokens or 0) * price_per_token(r[0].get("backend", ""))})
    return out


def backfill_sample(conn, d, version, n, seed="1"):
    """Queue the version for the first `n` rows of the shared order (`_order`) it hasn't judged: its sample, nested
    with the labels and with every other version's sample of the same seed. Returns rows queued."""
    q = sql.SQL("""
        INSERT INTO docket.queue (decision_id, version, row_key)
        SELECT %(d)s, %(v)s, x.k FROM (
            SELECT s.{k}::text AS k FROM {table} s
            LEFT JOIN docket.label l ON l.decision_id = %(d)s AND l.row_key = s.{k}::text
            ORDER BY {order} LIMIT %(n)s) x
        WHERE NOT EXISTS (SELECT 1 FROM docket.current c WHERE c.decision_id = %(d)s AND c.version = %(v)s
                          AND c.row_key = x.k)
        ON CONFLICT DO NOTHING""").format(k=sql.Identifier(d["key"]), table=sql.SQL(d["source"]), order=_order(d, seed))
    cur = conn.execute(q, {"d": d["id"], "v": int(version), "n": int(n), "seed": str(seed)})
    conn.execute("SELECT pg_notify('docket', %s)", (str(d["id"]),))
    return cur.rowcount


def draw_labels(conn, d, want, by=None, where=None, seed="1"):
    """The next rows to label in the shared order: for (group, n) in `want`, the first n rows of the group without a
    label that counts here, in md5(key || ':j:' || seed) order. With the labels so far they are a random sample of the
    table (import with `label --random`) or, by groups, of their group (`label --stratified COL`), and they come first
    in every version's sample. Returns [(key, group)]."""
    from .groups import _cond
    want = [(g, n) for g, n in want if n > 0]
    if not want:
        return []
    q = sql.SQL("""
        WITH want AS (SELECT * FROM unnest(%(g)s::text[], %(n)s::int[]) AS x(g, n)),
        cand AS (
            SELECT s.{k}::text AS k, {grp} AS g,
                   row_number() OVER (PARTITION BY {grp} ORDER BY md5(s.{k}::text || ':j:' || %(seed)s)) AS rn
            FROM {table} s LEFT JOIN docket.label l ON l.decision_id = %(d)s AND l.row_key = s.{k}::text
            WHERE ({cond}) AND NOT coalesce({counts}, false)
        )
        SELECT c.k, c.g FROM cand c JOIN want x ON x.g IS NOT DISTINCT FROM c.g WHERE c.rn <= x.n
        ORDER BY c.g NULLS LAST, c.rn""").format(
        k=sql.Identifier(d["key"]), grp=_group(by), counts=_counts(by), table=sql.SQL(d["source"]), cond=_cond(where))
    return [(k, g) for k, g in conn.execute(q, {"g": [g for g, _ in want], "n": [int(n) for _, n in want],
                                                "d": d["id"], "seed": str(seed), "by": by}).fetchall()]


def correlation(stats, i):
    """Model i's correlation with the labels, from the labelled rows it judged (smoothed as in `covariance`)."""
    cov = covariance(_restrict(stats, [LABEL, i]))
    return float(cov[0, 1] / math.sqrt(cov[0, 0] * cov[1, 1])) if cov[0, 0] > 0 and cov[1, 1] > 0 else 0.0


def estimate_with(conn, name, answer, wanted, where=None, level=0.95, target=None, label_cost=None, costs=None,
                  by=None):
    """`pgdocket estimate NAME --answer A --with V…`: the share from labels and several versions together, the same
    labels with each version alone and alone, and with --target and --label-cost the cheapest mix of labels and
    rows judged by each version. With `by` (#28), all of it per value of that source column, each group from its own
    labels and samples, and the whole scope from the groups."""
    from .estimate import norm_ppf
    from .groups import decision
    d = decision(conn, name)
    if d["spec"]["kind"] == "noul":
        answer = "yes"
    elif answer not in [o["name"] for o in d["spec"]["options"]]:
        raise SystemExit(f"estimate --with needs --answer, one of: {', '.join(o['name'] for o in d['spec']['options'])}")
    vs = version_info(conn, d, wanted, costs)
    z = norm_ppf(0.5 + level / 2)
    ids = [v["version"] for v in vs]
    head = {"name": name, "answer": answer, "level": level, "where": where, "by": by}
    if not by:
        stats, rows = ledger_stats(conn, d, ids, answer, where)
        return {**together(stats, rows, vs, z, target, label_cost), **head}
    per = [(g, together(st, n, [dict(v) for v in vs], z, target, label_cost))
           for g, st, n in ledger_stats(conn, d, ids, answer, where, by)]
    return {**by_groups(per, vs, z, target, label_cost), **head}


def together(stats, rows, vs, z, target=None, label_cost=None):
    """What `estimate_with` reports, from the statistics: the estimate from every source, each version with the
    labels, the labels alone, and the cheapest mix for ± `target` when labels have a cost. `vs`: the versions in the
    order of the statistics' sources, as `version_info` gives them (rows, says and alone are filled in here). With
    fewer than PILOT labels the correlations aren't known yet, and the plan is a pilot (`_pilot`); once the interval
    is within ± `target` (and has MIN_EACH_SIDE labels each way), nothing more is needed."""
    from .groups import PILOT
    est = estimate(stats, z, rows)
    for i, v in enumerate(vs, 1):
        n = sum(p["n"] for p in stats["patterns"] if i in p["mask"])
        v["rows"] = n
        v["says"] = sum(p["sum"][p["mask"].index(i)] for p in stats["patterns"] if i in p["mask"]) / n if n else None
        v["alone"] = estimate(_restrict(stats, [LABEL, i]), z, rows)
        v["rho"] = correlation(stats, i) if v["alone"]["theta"] is not None else None
    labels = estimate(_restrict(stats, [LABEL]), z, rows)
    out = {"rows": rows, "versions": vs, "estimate": est, "labels": labels, "plan": None, "target": target,
           "label_cost": label_cost}
    if target and label_cost is not None and rows:
        if est["labels"] < min(PILOT, rows):
            out["plan"], out["planned"] = [_pilot(vs, rows, est["labels"], label_cost)], [f"v{v['version']}" for v in vs]
            return out
        usable = [v for v in vs if v["rho"] is not None and v["rho"] > 0]
        # labels all on one side (a rare answer): the share is about 1 in (labels + 2) (Laplace's rule of succession)
        share = next((e["theta"] for e in (est, labels) if e["theta"] is not None),
                     (est["labels_with"] + 1) / (est["labels"] + 2))
        names = [f"v{v['version']}" for v in usable]
        out["plan"] = plan(share, [v["rho"] for v in usable], [v["cost"] for v in usable], label_cost, rows, target, z,
                           names, labelled=est["labels"], judged=[v["rows"] for v in usable])
        out["planned"] = names
        if est["theta"] is not None and est["reliable"] and z * est["se"] <= target:
            used = [n for n in est.get("used", []) if n in names]
            out["plan"].insert(0, {"kind": "met", "labels": est["labels"], "models": used, "more_labels": 0,
                                   "rows": {n: v["rows"] for n, v in zip(names, usable) if n in used},
                                   "more_rows": {}, "cost": 0.0})
    return out


def _pilot(vs, rows, labelled, label_cost):
    """The plan while fewer than PILOT labels count: PILOT of them first, each version judging those rows (its sample
    starts with them), and the cheapest mix is planned from what they show. When PILOT is every row, every row
    labelled: exact, and no version needs to judge more."""
    from .groups import PILOT
    m0 = min(rows, PILOT)
    names = [f"v{v['version']}" for v in vs]
    judge = {} if m0 >= rows else {n: max(v["rows"], m0) for n, v in zip(names, vs)}
    more = {n: m - v["rows"] for (n, m), v in zip(judge.items(), vs)}
    cost = label_cost * max(0, m0 - labelled) + sum(v["cost"] * more.get(n, 0) for n, v in zip(names, vs))
    return {"kind": "exact" if m0 >= rows else "pilot", "labels": m0, "rows": judge, "models": list(judge),
            "more_labels": max(0, m0 - labelled), "more_rows": more, "cost": cost}


def _pick(plans, kind, models=None):
    """The plan of this kind (and models); or a pilot or exact plan, which every way starts with; or none needed."""
    if plans and plans[0]["kind"] in ("pilot", "exact", "met"):
        return plans[0]
    return next((p for p in plans or [] if p["kind"] == kind and (models is None or p["models"] == models)), None)


def by_groups(per, vs, z, target=None, label_cost=None):
    """What `estimate_with --by` reports, from each group's `together`: the groups; the whole scope as their estimates
    weighted by their rows (a stratified estimate, when every group has one); the groups where the active version's
    error differs from the others' (`groups.systematic`); and, with a plan, each group's cheapest mix and the totals,
    with labels alone in every group and with the active version on every row (`estimate --by`) beside them."""
    from .groups import systematic
    gs = [{"g": g, **r} for g, r in per]
    live = [r for r in gs if r["rows"]]
    total = sum(r["rows"] for r in live)
    overall = None
    if live and all(r["estimate"]["theta"] is not None for r in live):
        theta = sum(r["rows"] / total * r["estimate"]["theta"] for r in live)
        se = math.sqrt(sum((r["rows"] / total * r["estimate"]["se"]) ** 2 for r in live))
        overall = {"theta": theta, "se": se, "ci": (max(0.0, theta - z * se), min(1.0, theta + z * se)),
                   "rows": total, "reliable": all(r["estimate"]["reliable"] for r in live)}
    flags = systematic([{"g": r["g"], "reliable": r["estimate"]["reliable"], "est": r["estimate"],
                         "model_share": r["versions"][0]["says"]}
                        for r in live if r["estimate"]["theta"] is not None and r["versions"][0]["says"] is not None])
    out = {"groups": gs, "rows": total, "versions": vs, "overall": overall, "systematic": flags, "target": target,
           "label_cost": label_cost, "totals": None}
    if target and label_cost is not None and live:
        active = f"v{vs[0]['version']}"
        ways = {"cheapest": lambda ps: ps[0] if ps else None, "labels alone": lambda ps: _pick(ps, "labels alone"),
                "active": lambda ps: _pick(ps, "every row", [active])}
        totals = {}
        for way, pick in ways.items():
            chosen = [pick(r["plan"]) for r in live]
            if any(p is None for p in chosen):
                continue
            more_rows = {}
            for p in chosen:
                for n, m in p["more_rows"].items():
                    more_rows[n] = more_rows.get(n, 0) + m
            totals[way] = {"labels": sum(p["labels"] for p in chosen), "more_labels": sum(p["more_labels"] for p in chosen),
                           "more_rows": more_rows, "cost": sum(p["cost"] for p in chosen),
                           "pilots": sum(p["kind"] == "pilot" for p in chosen)}
        out["totals"] = totals
    return out


def render(r):
    """Plain-text report for the CLI."""
    if r.get("by"):
        return render_by(r)
    pct = lambda x: "–" if x is None else f"{100 * x:.1f} %"
    ci = lambda e: ("exact" if e["exact"] else f"{100 * e['ci'][0]:.1f} – {100 * e['ci'][1]:.1f}") if e["ci"] else "–"
    lvl = f"{100 * r['level']:.0f} %"
    vs, est = r["versions"], r["estimate"]
    L = [f"{r['name']}: share of rows whose true answer is {r['answer']!r}, from labels and {len(vs)} versions "
         f"({lvl} intervals){', where ' + r['where'] if r['where'] else ''}; {r['rows']:,} rows",
         f"{'source':<36} {'rows':>11} {'says':>8}"]
    for v in vs:
        tag = f"v{v['version']} {v['model'] or ''}" + (" (active)" if v["status"] == "active" else "")
        L.append(f"{tag:<36.36} {v['rows']:>11,} {pct(v['says']):>8}")
    L.append(f"{'labels drawn at random':<36} {est['labels']:>11,} "
             f"{pct(est['labels_with'] / est['labels'] if est['labels'] else None):>8}")
    L.append(f"{'estimate, every source together':<36} {'':>11} {pct(est['theta']):>8}  {ci(est)}"
             + ("" if est["reliable"] or est["theta"] is None else "  ⚠"))
    for v in vs:
        a = v["alone"]
        L.append(f"{'  v' + str(v['version']) + ' with the labels':<36} {'':>11} {pct(a['theta']):>8}  {ci(a)}")
    L.append(f"{'  the labels alone':<36} {'':>11} {pct(r['labels']['theta']):>8}  {ci(r['labels'])}")
    left = [s for s in ["v" + str(v["version"]) for v in vs] if est.get("used") and s not in est["used"]]
    if left:
        L.append(f"left out: {', '.join(left)} (it says the same thing on every row, or judged fewer than "
                 f"{MIN_EACH_SIDE} labelled rows with and without the answer)")
    if est["theta"] is not None and not est["reliable"]:
        L.append(f"⚠ fewer than {MIN_EACH_SIDE} labelled rows with or without the answer: the interval may be too narrow")
    if r["plan"]:
        names = r["planned"]
        per = ", ".join(f"{n} {'free' if not c else '$' + format(c, '.6f')}"
                        for n, c in zip(names, [v["cost"] for v in vs if f"v{v['version']}" in names]))
        L += ["", f"the cheapest mix for ±{100 * r['target']:.1f} points (a label ${r['label_cost']:,.2f}; per row {per}):",
              f"  {'plan':<44} {'labels':>8} " + " ".join(f"{n:>10}" for n in names) + f" {'still costs':>12}"]
        shown, seen = [], set()
        for p in r["plan"]:
            what = _what(p, r["rows"])
            if what in shown or (p["labels"], round(p["cost"], 2)) in seen:
                continue                                     # e.g. a free model added where it changes nothing
            shown.append(what)
            seen.add((p["labels"], round(p["cost"], 2)))
            L.append(f"  {what:<44.44} {p['labels']:>8,} " + " ".join(
                f"{p['rows'][n]:>10,}" if n in p["rows"] else f"{'–':>10}" for n in names)
                + f" {'$' + format(p['cost'], ',.2f'):>12}")
            if len(shown) == 5:
                break
        L.append(("then plan again: " if r["plan"][0]["kind"] == "pilot" else "cheapest: ") + _steps(r["plan"][0]))
    return "\n".join(L)


def _what(p, rows):
    """A plan in words."""
    if p["kind"] == "pilot":
        return f"pilot: {p['labels']} labels first"
    if p["kind"] == "exact":
        return "label every row: exact"
    if p["kind"] == "met":
        return "nothing more: the target is met"
    return "labels alone" if not p["models"] else "labels, " + ", ".join(
        f"{m} on {'every row' if p['rows'][m] >= rows else 'a sample'}" for m in p["models"])


def _steps(t):
    """What a plan (or the plans' totals) still needs, in words."""
    steps = [f"{t['more_labels']:,} more label{'s' * (t['more_labels'] != 1)}"] * bool(t["more_labels"]) + [
        f"{m:,} more row{'s' * (m != 1)} judged by {n}" for n, m in t["more_rows"].items() if m]
    return ", ".join(steps) if steps else "nothing more; the target is met"


def render_by(r):
    """Plain-text report for the CLI, by group."""
    pct = lambda x: "–" if x is None else f"{100 * x:.1f} %"
    ci = lambda e: ("exact" if e["exact"] else f"{100 * e['ci'][0]:.1f} – {100 * e['ci'][1]:.1f}") if e and e["ci"] else "–"
    lvl = f"{100 * r['level']:.0f} %"
    vs, gs = r["versions"], r["groups"]
    name = lambda g: "(none)" if g is None else str(g)
    partial = [i for i, v in enumerate(vs) if any(x["versions"][i]["rows"] < x["rows"] for x in gs)]
    L = [f"{r['name']}: share of rows whose true answer is {r['answer']!r}, by {r['by']}, from labels and {len(vs)} "
         f"versions ({lvl} intervals){', where ' + r['where'] if r['where'] else ''}; {r['rows']:,} rows",
         f"{'group':<20} {'rows':>9} {'labels':>7}" + "".join(
             (f" {'v' + str(v['version']) + ' rows':>9}" if i in partial else "") + f" {'v' + str(v['version']) + ' says':>8}"
             for i, v in enumerate(vs)) + f" {'estimate':>9} {lvl + ' interval':>16} {'labels alone':>13}"]
    for x in gs:
        e = x["estimate"]
        L.append(f"{name(x['g']):<20.20} {x['rows']:>9,} {e['labels']:>7,}" + "".join(
            (f" {x['versions'][i]['rows']:>9,}" if i in partial else "") + f" {pct(x['versions'][i]['says']):>8}"
            for i in range(len(vs))) + f" {pct(e['theta']):>9} {ci(e):>16} {ci(x['labels']):>13}"
            + ("" if e["reliable"] or e["theta"] is None else "  ⚠"))
    o = r["overall"]
    if o and len(gs) > 1:
        L.append(f"{'all groups':<20} {o['rows']:>9,} {sum(x['estimate']['labels'] for x in gs):>7,}"
                 + "".join((f" {'':>9}" if i in partial else "") + f" {'':>8}" for i in range(len(vs)))
                 + f" {pct(o['theta']):>9} {ci({'exact': o['se'] == 0, 'ci': o['ci']}):>16}"
                 + ("" if o["reliable"] else f" {'':>13}  ⚠"))
    L.append("versions: " + ", ".join(f"v{v['version']} {v['model'] or ''}".rstrip()
                                      + (" (active)" if v["status"] == "active" else "") for v in vs)
             + f"; labels: drawn at random, from the table or within each {r['by']}")
    left = {}                                                # versions left out → the groups where they were
    for x in gs:
        out_ = tuple(f"v{v['version']}" for v in vs if x["estimate"].get("used") and
                     f"v{v['version']}" not in x["estimate"]["used"])
        if out_:
            left.setdefault(out_, []).append(name(x["g"]))
    if left:
        L.append("left out: " + "; ".join(f"{' and '.join(m)} in {', '.join(g)}" for m, g in left.items())
                 + f" (it says the same thing on every row there, or judged fewer than {MIN_EACH_SIDE} labelled rows "
                 "with and without the answer)")
    if any(x["estimate"]["theta"] is not None and not x["estimate"]["reliable"] for x in gs):
        L.append(f"⚠ fewer than {MIN_EACH_SIDE} labelled rows with or without the answer: the interval may be too narrow")
    if any(x["estimate"]["theta"] is None and x["rows"] for x in gs):
        L.append("– : too few labels in that group to estimate it (fewer than 2, or all the same)")
    for f in r["systematic"]:
        side = "overstates" if f["bias"] > 0 else "understates"
        L.append(f"! v{vs[0]['version']} {side} {r['answer']!r} by {abs(100 * f['bias']):.1f} points in {f['group']} "
                 f"(by {100 * f['pooled']:+.1f} in the other groups, z = {f['z']:.1f}): its error differs by {r['by']}, "
                 "so one correction for every group would be off there. Each group is corrected with its own labels.")
    t = r["totals"]
    if t is not None:
        names = [f"v{v['version']}" for v in vs]
        per = ", ".join(f"{n} {'free' if not v['cost'] else '$' + format(v['cost'], '.6f')}" for n, v in zip(names, vs))
        L += ["", f"the cheapest mix for ±{100 * r['target']:.1f} points in every group (a label "
                  f"${r['label_cost']:,.2f}; per row {per}):",
              f"{'group':<20} {'plan':<40} {'labels':>8} " + " ".join(f"{n:>9}" for n in names) + f" {'still costs':>12}"]
        for x in gs:
            if not x["plan"]:
                continue
            p = x["plan"][0]
            L.append(f"{name(x['g']):<20.20} {_what(p, x['rows']):<40.40} {p['labels']:>8,} " + " ".join(
                f"{p['rows'][n]:>9,}" if n in p["rows"] else f"{'–':>9}" for n in names)
                + f" {'$' + format(p['cost'], ',.2f'):>12}")
        labels = {"cheapest": "in all: each group's cheapest mix", "labels alone": "  labels alone in every group",
                  "active": f"  labels, {names[0]} on every row (estimate --by)"}
        for way, tag in labels.items():
            if way in t:
                L.append(f"{tag:<61.61} {t[way]['labels']:>8,} " + " ".join(f"{'':>9}" for _ in names)
                         + f" {'$' + format(t[way]['cost'], ',.2f'):>12}")
        best = t.get("cheapest")
        if best:
            k = best["pilots"]
            L.append(("cheapest: " + _steps(best)) + (
                f"; {k} group{'s need' if k > 1 else ' needs'} a pilot first, then plan again" if k else ""))
    return "\n".join(L)
