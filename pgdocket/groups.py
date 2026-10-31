"""Shares by group (`pgdocket estimate --by COL`): how far each group's figure can be trusted, and where the next
labels should go (#25).

A GROUP BY result is only as good as its least reliable group, and two things decide that, neither of them the
model's confidence:
- **Systematic error.** A model can be wrong more often in some groups than in others (two products confused more in
  one region). One correction fitted on all the labels moves every group by the same amount and is biased wherever the
  model is wrong more or less often than on average (Mozer, 2026). So each group is corrected with its own labels:
  prediction-powered inference (PPI++) within the group, which is unbiased whatever the model gets wrong there.
- **Where the labels fall.** A group's interval narrows with *its* labels. Labels drawn at random across the table
  arrive in proportion to group size, so the smallest group decides how many are needed. Giving each group the labels
  its own target needs, given how often the model disagrees with people there, costs far less: a Neyman allocation, as
  in stratified PPI (Fisch et al., 2024) and in allocating human oversight across segments (Ye, Lyu & Tao, 2026).

Labels drawn within groups (`--draw`, imported with `label --stratified COL`) are a uniform sample of their group, so
they count for that column's groups only: estimates of the whole table, calibration and drift keep to random labels.
"""
import csv
import math
import os

from psycopg import sql

from .estimate import MIN_EACH_SIDE, norm_ppf

PILOT = 30            # labels a group gets before its own disagreement rate is trusted for planning
ALPHA = 0.05          # family-wise level of the systematic-error flags


def ppi_binary(n, k, sf_l, syf, nu, sf_u):
    """PPI++ for a share, from counts of 0/1 answers: n labelled rows, k labelled with the answer, sf_l of them answered
    with it by the model and syf by both; nu unlabelled rows, sf_u answered with it by the model. The formulas of
    `estimate.ppi_share` and docket.estimate(). With every row labelled the share is exact. None when the labels can't
    estimate it (fewer than 2, or all the same)."""
    if nu == 0 and n:
        return {"theta": k / n, "se": 0.0, "lam": 0.0, "res_var": 0.0, "exact": True}
    if n < 2 or k in (0, n):
        return None
    ybar, fl = k / n, sf_l / n
    var_y = n * ybar * (1 - ybar) / (n - 1)
    var_fl = n * fl * (1 - fl) / (n - 1)
    cov = (syf - n * ybar * fl) / (n - 1)
    if nu < 2:
        return {"theta": ybar, "se": math.sqrt(var_y / n), "lam": 0.0, "res_var": var_y, "exact": False}
    fu, N = sf_u / nu, n + nu
    var_fu = nu * fu * (1 - fu) / (nu - 1)
    fa = (sf_l + sf_u) / N
    var_f = N * fa * (1 - fa) / (N - 1)
    lam = 0.0 if var_f <= 0 else min(1.0, max(0.0, cov / ((1 + n / nu) * var_f)))
    res = max(0.0, var_y + lam * lam * var_fl - 2 * lam * cov)        # per label: what the model leaves unexplained
    return {"theta": ybar + lam * (fu - fl), "se": math.sqrt(res / n + lam * lam * var_fu / nu), "lam": lam,
            "res_var": res, "exact": False}


def summarise(stats, z):
    """Per-group estimates from `group_stats` rows (dicts with g, judged, n, k, sf_l, syf, sf_u)."""
    out = []
    for s in stats:
        g = dict(s)
        est = ppi_binary(g["n"], g["k"], g["sf_l"], g["syf"], g["judged"] - g["n"], g["sf_u"]) if g["judged"] else None
        g["model_share"] = (g["sf_l"] + g["sf_u"]) / g["judged"] if g["judged"] else None
        g["est"] = est
        g["reliable"] = bool(est) and (est["exact"] or min(g["k"], g["n"] - g["k"]) >= MIN_EACH_SIDE)
        if est:
            g["ci"] = (max(0.0, est["theta"] - z * est["se"]), min(1.0, est["theta"] + z * est["se"]))
        out.append(g)
    return out


def combine(groups, z):
    """The whole scope as the groups' estimates weighted by their judged rows (a stratified estimate). None unless every
    group with judged rows has an estimate."""
    live = [g for g in groups if g["judged"]]
    if not live or any(g["est"] is None for g in live):
        return None
    total = sum(g["judged"] for g in live)
    theta = sum(g["judged"] / total * g["est"]["theta"] for g in live)
    se = math.sqrt(sum((g["judged"] / total * g["est"]["se"]) ** 2 for g in live))
    return {"theta": theta, "se": se, "ci": (max(0.0, theta - z * se), min(1.0, theta + z * se)), "judged": total,
            "reliable": all(g["reliable"] for g in live)}


def systematic(groups, alpha=ALPHA):
    """Groups where the model's error differs from its error in the other groups: these are the groups one correction
    for all of them would get wrong. The model's share minus the estimate in each group is compared with the
    inverse-variance mean of the other groups' (a z-test, Bonferroni across the groups still in play). Step-down: the
    most extreme group is flagged and set aside before testing the rest, so one badly wrong group can't make a fair
    one look wrong by dragging the mean."""
    usable = [g for g in groups if g["reliable"] and g["est"]["se"] > 0]
    flags = []
    while len(usable) >= 2:
        crit = norm_ppf(1 - alpha / (2 * len(usable)))
        worst = None
        for g in usable:
            others = [h for h in usable if h is not g]
            w = [1 / h["est"]["se"] ** 2 for h in others]
            rest = sum(wi * (h["model_share"] - h["est"]["theta"]) for wi, h in zip(w, others)) / sum(w)
            b = g["model_share"] - g["est"]["theta"]
            z = (b - rest) / math.sqrt(g["est"]["se"] ** 2 + 1 / sum(w))
            if worst is None or abs(z) > abs(worst["z"]):
                worst = {"group": g["g"], "bias": b, "pooled": rest, "z": z, "_g": g}
        if abs(worst["z"]) <= crit:
            break
        usable.remove(worst.pop("_g"))
        flags.append(worst)
    return flags


def plan(groups, target, z):
    """Labels each group needs for an interval of ± `target` (a share), drawn at random within the group.

    A group with fewer than PILOT labels gets a pilot first: its disagreement rate isn't known yet. Otherwise it needs
    z² · (the per-label variance PPI++ leaves) / target² labels, and at least MIN_EACH_SIDE with and without the answer
    for the interval to hold; never more than its judged rows (then the share is exact). Also returns how many labels
    drawn at random across the whole scope would give every group as many, when every group is past its pilot."""
    rows = []
    for g in groups:
        J, n, est = g["judged"], g["n"], g["est"]
        if not J:
            continue
        if n >= J:
            need, kind = J, "exact"
        elif n < PILOT:
            need = min(J, PILOT)
            kind = "exact" if need >= J else "pilot"
        else:
            # every label the same so far (a rare answer): about MIN_EACH_SIDE / p rows are needed to see it often enough
            p = 1 / (n + 2) if est is None else min(max(est["theta"], 1 / (n + 2)), 1 - 1 / (n + 2))
            width = 0 if est is None else math.ceil(z * z * est["res_var"] / target ** 2)
            enough = math.ceil(MIN_EACH_SIDE / min(p, 1 - p))
            need = min(J, max(n, width, enough))
            kind = "exact" if need >= J else "target"
        rows.append({"group": g["g"], "judged": J, "labels": n, "need": need, "more": max(0, need - n), "kind": kind})
    total_judged = sum(r["judged"] for r in rows)
    uniform = setter = None
    if rows and all(r["kind"] != "pilot" for r in rows):
        setter = max(rows, key=lambda r: r["need"] / r["judged"])         # the group random labels reach last
        uniform = min(total_judged, math.ceil(setter["need"] * total_judged / setter["judged"]))
    return {"target": target, "groups": rows, "more": sum(r["more"] for r in rows),
            "need": sum(r["need"] for r in rows), "uniform": uniform, "uniform_set_by": setter and setter["group"]}


# ---------------------------------------------------------------- database
def decision(conn, name):
    r = conn.execute("SELECT d.id, d.source, d.key_column, d.text_columns, v.spec FROM docket.decision d "
                     "JOIN docket.version v ON v.decision_id = d.id AND v.version = d.active_version "
                     "WHERE d.name = %s", (name,)).fetchone()
    if not r:
        raise SystemExit(f"no decision named {name!r}")
    return {"id": r[0], "source": r[1], "key": r[2], "text": r[3], "spec": r[4], "name": name}


def _cond(where):
    """The caller's SQL condition on the source table, as `estimate --where` takes it (% doubled for the driver)."""
    return sql.SQL(where.replace("%", "%%")) if where and where.strip() else sql.SQL("true")


def group_stats(conn, d, by, answer, where=None):
    """One pass over the scope: per value of `by`, the counts `ppi_binary` needs. Labels count when drawn at random
    from the whole table or within this column's groups."""
    q = sql.SQL("""
        WITH scope AS (
            SELECT s.{by}::text AS g, v.status, (v.answer = %(ans)s)::int AS f, s.{key}::text AS k
            FROM {table} s JOIN {view} v ON v.key = s.{key}
            WHERE {cond}
        ), lab AS (
            SELECT row_key, (label = %(ans)s)::int AS y FROM docket.label
            WHERE decision_id = %(d)s AND (sampling = 'random' OR (sampling = 'stratified' AND %(by)s = ANY(strata)))
        )
        SELECT sc.g, count(*) AS in_scope,
               count(*) FILTER (WHERE sc.status = 'judged') AS judged,
               count(l.y) FILTER (WHERE sc.status = 'judged') AS n,
               coalesce(sum(l.y) FILTER (WHERE sc.status = 'judged'), 0) AS k,
               coalesce(sum(sc.f) FILTER (WHERE sc.status = 'judged' AND l.y IS NOT NULL), 0) AS sf_l,
               coalesce(sum(sc.f * l.y) FILTER (WHERE sc.status = 'judged'), 0) AS syf,
               coalesce(sum(sc.f) FILTER (WHERE sc.status = 'judged' AND l.y IS NULL), 0) AS sf_u
        FROM scope sc LEFT JOIN lab l ON l.row_key = sc.k
        GROUP BY sc.g ORDER BY sc.g NULLS LAST""").format(
        by=sql.Identifier(by), key=sql.Identifier(d["key"]), table=sql.SQL(d["source"]),
        view=sql.Identifier("docket", "v_" + d["name"]), cond=_cond(where))
    cur = conn.execute(q, {"ans": answer, "d": d["id"], "by": by})
    cols = [c.name for c in cur.description]
    return [{c: (int(v) if c not in ("g",) and v is not None else v) for c, v in zip(cols, r)} for r in cur.fetchall()]


def estimate_by(conn, name, by, answer=None, where=None, level=0.95, target=None):
    d = decision(conn, name)
    if d["spec"]["kind"] == "noul":
        answer = "yes"
    else:
        names = [o["name"] for o in d["spec"]["options"]]
        if answer is None:
            raise SystemExit("estimate --by needs --answer: the option whose share to estimate in each group")
        if answer not in names:
            raise SystemExit(f"{answer!r} is not an option of {name}: {', '.join(names)}")
    z = norm_ppf(0.5 + level / 2)
    groups = summarise(group_stats(conn, d, by, answer, where), z)
    return {"name": name, "by": by, "answer": answer, "level": level, "where": where, "groups": groups,
            "overall": combine(groups, z), "systematic": systematic(groups),
            "plan": plan(groups, target, z) if target else None}


def draw(conn, name, by, plan_, where=None, seed="1"):
    """The rows to label next: for each group, `more` rows drawn at random among its judged rows that are not labelled
    at random, from a whole column, or for this column already. Returns [(key, group)]."""
    d = decision(conn, name)
    want = [(r["group"], r["more"]) for r in plan_["groups"] if r["more"] > 0]
    if not want:
        return d, []
    q = sql.SQL("""
        WITH want AS (SELECT * FROM unnest(%(g)s::text[], %(m)s::int[]) AS w(g, m)),
        cand AS (
            SELECT s.{key}::text AS k, s.{by}::text AS g,
                   row_number() OVER (PARTITION BY s.{by}::text ORDER BY md5(s.{key}::text || %(seed)s)) AS rn
            FROM {table} s JOIN {view} v ON v.key = s.{key}
            WHERE v.status = 'judged' AND ({cond}) AND NOT EXISTS (
                SELECT 1 FROM docket.label l WHERE l.decision_id = %(d)s AND l.row_key = s.{key}::text
                  AND (l.sampling IN ('random', 'all') OR (l.sampling = 'stratified' AND %(by)s = ANY(l.strata))))
        )
        SELECT c.k, c.g FROM cand c JOIN want w ON w.g IS NOT DISTINCT FROM c.g WHERE c.rn <= w.m
        ORDER BY c.g NULLS LAST, c.rn""").format(
        by=sql.Identifier(by), key=sql.Identifier(d["key"]), table=sql.SQL(d["source"]),
        view=sql.Identifier("docket", "v_" + name), cond=_cond(where))
    rows = conn.execute(q, {"g": [g for g, _ in want], "m": [m for _, m in want], "seed": f":{by}:{seed}",
                            "d": d["id"], "by": by}).fetchall()
    return d, rows


def export(conn, d, by, rows, path):
    """Write the drawn rows to label: key, group, the text columns, an empty label. Blind: no AI answer."""
    keys = [k for k, _ in rows]
    q = sql.SQL("SELECT {k}::text, {cols} FROM {t} WHERE {k}::text = ANY(%s)").format(
        k=sql.Identifier(d["key"]), cols=sql.SQL(", ").join(sql.Identifier(c) for c in d["text"]), t=sql.SQL(d["source"]))
    text = {r[0]: r[1:] for r in conn.execute(q, (keys,)).fetchall()}
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["key", by] + list(d["text"]) + ["label"])
        for k, g in rows:
            w.writerow([k, "" if g is None else g] + list(text.get(k, [""] * len(d["text"]))) + [""])
    return len(rows)


def render(r, plan_rows=None):
    """Plain-text table for the CLI."""
    pct = lambda x: "–" if x is None else f"{100 * x:.1f} %"
    lvl = f"{100 * r['level']:.0f} %"
    L = [f"{r['name']}: share of judged rows whose true answer is {r['answer']!r}, by {r['by']} ({lvl} intervals)"
         + (f", where {r['where']}" if r["where"] else "")]
    tgt = r["plan"]
    head = f"{'group':<20} {'rows':>7} {'labels':>7} {'model says':>11} {'estimate':>9} {lvl + ' interval':>18}"
    if tgt:
        head += f"  {'labels for ±' + format(100 * tgt['target'], '.1f') + ' pts':>22}"
    L.append(head)
    plans = {p["group"]: p for p in (tgt["groups"] if tgt else [])}
    for g in r["groups"]:
        est = g["est"]
        ci = "exact" if est and est["exact"] else (f"{100 * g['ci'][0]:.1f} – {100 * g['ci'][1]:.1f}" if est else "–")
        line = (f"{str(g['g']) if g['g'] is not None else '(none)':<20.20} {g['judged']:>7,} {g['n']:>7,} "
                f"{pct(g['model_share']):>11} {pct(est['theta'] if est else None):>9} {ci:>18}")
        if tgt and g["g"] in plans:
            p = plans[g["g"]]
            note = {"pilot": f"+{p['more']} (pilot)", "exact": f"+{p['more']} (all: exact)", "target": f"+{p['more']}"}[p["kind"]]
            line += f"  {note:>22}"
        L.append(line + ("" if g["reliable"] or not est else "  ⚠"))
    o = r["overall"]
    if o:
        L.append(f"{'all groups':<20} {o['judged']:>7,} {'':>7} {'':>11} {pct(o['theta']):>9} "
                 f"{100 * o['ci'][0]:.1f} – {100 * o['ci'][1]:.1f}".rstrip())
    if any(g["est"] and not g["reliable"] for g in r["groups"]):
        L.append(f"⚠ fewer than {MIN_EACH_SIDE} labelled rows with or without the answer: the interval may be too narrow")
    if any(g["est"] is None and g["judged"] for g in r["groups"]):
        L.append("– : too few labels in that group to estimate it (fewer than 2, or all the same)")
    for f in r["systematic"]:
        side = "overstates" if f["bias"] > 0 else "understates"
        L.append(f"! the model {side} {r['answer']!r} by {abs(100 * f['bias']):.1f} points in {f['group']} "
                 f"(by {100 * f['pooled']:+.1f} in the other groups, z = {f['z']:.1f}): its error differs by {r['by']}, so a "
                 "correction fitted on all labels would be off there. Each group is corrected with its own labels.")
    if tgt:
        L.append(f"to reach ±{100 * tgt['target']:.1f} points in every group: {tgt['more']:,} more labels, drawn at random "
                 f"within each group ({tgt['need']:,} in all)" + (
                     f"; drawn at random across all groups instead, about {tgt['uniform']:,} would be needed "
                     f"(set by {tgt['uniform_set_by']}, which random labels reach last)"
                     if tgt["uniform"] else "; groups marked pilot need a first draw before the rest can be planned"))
    return "\n".join(L)
