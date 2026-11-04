"""Several models for a weighted share, live (#29): a real join in Postgres, real calls to Laya and Jev.

The 2,000 CFPB narratives judged in Phase 0 go into demo.cfpb_texts, one row each with the product the consumer chose,
and the 21,980 complaints in the source shard that carry them word for word into demo.cfpb_complaints. The query is the
share of these complaints about X: a join with fan-out, each text counted by
    (SELECT count(*) FROM demo.cfpb_complaints c WHERE c.text_id = s.id).
A decision on the texts has Laya (open weights, run on this machine) as its active version, judging every text, and Jev
1.14 one row per request as version 2. Then, answer by answer, `pgdocket estimate --answer X --weight … --with 2
--target T --label-cost 1 --queue --draw` round after round, as a user would run it: the worker judges what is queued,
and the drawn texts are labelled with the product their complaint chose, as a person reading them would. The Jev calls
are real and capped by --max-usd; everything lives in the ledger, so a second run picks up where the first stopped.

Needs the db and laya services (`docker compose --profile laya up -d --wait laya`) and the source shard.
"""
import contextlib
import csv
import hashlib
import io
import json
import os
import tempfile

import numpy as np

from pgdocket import cli, ledger
from pgdocket import groups as G
from pgdocket import multiweight as MW
from pgdocket.backend import USD_PER_INPUT_TOKEN
from pgdocket.estimate import norm_ppf
from pgdocket.worker import Worker

from .groupvalidate import REGION, RUNS, SHARD
from .tasks import PRODUCT_MAP, PRODUCT_OPTIONS, PRODUCT_QUESTION

OUT = "results/multiweight-live"
NAME = "cfpb_join"
LAYA = "http://laya:8000/v1/systemone"
WEIGHT = "(SELECT count(*) FROM demo.cfpb_complaints c WHERE c.text_id = s.id)"
ANSWERS = ("credit reporting", "debt collection", "credit card")
SHEET = os.path.join(tempfile.gettempdir(), "livejoin-next.csv")   # in the container: the drawn narratives, never kept
ROUNDS = 8
Z = norm_ppf(0.975)


def build(dsn, log):
    """demo.cfpb_texts and demo.cfpb_complaints, once."""
    import pyarrow.parquet as pq
    with ledger.connect(dsn) as conn:
        if conn.execute("SELECT to_regclass('demo.cfpb_complaints')").fetchone()[0]:
            return
        conn.execute("CREATE SCHEMA IF NOT EXISTS demo")
        conn.execute("CREATE TABLE demo.cfpb_texts AS SELECT row_number() OVER (ORDER BY complaint_id)::int AS id, "
                     "complaint_id, narrative, product, text_hash FROM bench.complaints")
        conn.execute("ALTER TABLE demo.cfpb_texts ADD PRIMARY KEY (id)")
        ids = dict(conn.execute("SELECT text_hash, id FROM demo.cfpb_texts").fetchall())
        t = pq.read_table(SHARD, columns=["Complaint ID", "Consumer complaint narrative", "State", "Product"])
        rows = []
        for cid, text, state, prod in zip(*(t.column(c).to_pylist() for c in
                                            ("Complaint ID", "Consumer complaint narrative", "State", "Product"))):
            s = (text or "").strip()
            h = hashlib.md5(s[:1500].encode("utf-8")).hexdigest() if len(s) >= 40 else None
            if h in ids:
                rows.append((str(cid), ids[h], state, REGION.get(state, "other or unknown"), PRODUCT_MAP.get(prod or "")))
        conn.execute("CREATE TABLE demo.cfpb_complaints (complaint_id text, text_id int NOT NULL REFERENCES "
                     "demo.cfpb_texts, state text, region text, product text)")
        with conn.cursor() as cur:
            cur.executemany("INSERT INTO demo.cfpb_complaints VALUES (%s, %s, %s, %s, %s)", rows)
        conn.execute("CREATE INDEX ON demo.cfpb_complaints (text_id)")
    log(f"demo.cfpb_texts: {len(ids):,} texts; demo.cfpb_complaints: {len(rows):,} complaints")


def decide(dsn, log):
    """The decision on the texts, Laya judging every one (free; about a third of a second a text here), and Jev 1.14
    one row per request as a second version that judges nothing yet. Returns v2."""
    with ledger.connect(dsn) as conn:
        exists = conn.execute("SELECT 1 FROM docket.decision WHERE name = %s", (NAME,)).fetchone()
    if not exists:
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump([{"name": o} for o in PRODUCT_OPTIONS], f)
        spec = ledger.load_spec("choice", PRODUCT_QUESTION, None, f.name, LAYA, "laya", 1)
        _, queued = ledger.create(dsn, NAME, "demo.cfpb_texts", "id", ["narrative"], spec)
        log(f"{NAME} created: {queued:,} texts queued for Laya")
    with ledger.connect(dsn) as conn:
        d = G.decision(conn, NAME)
        v2 = conn.execute("SELECT max(version) FROM docket.version WHERE decision_id = %s AND version > 1",
                          (d["id"],)).fetchone()[0]
    if v2 is None:                                       # only Laya's rows are queued: no key, nothing paid
        Worker(dsn, concurrency=4, claim_size=50, poll=0.5, log=log).run(once=True)
        v2, _ = ledger.revise(dsn, NAME, model="jev-1.14.0", backend="jev")
        log(f"v{v2}: jev-1.14.0, one row per request; it judges only what a plan queues")
    return v2


def spent(dsn, v2):
    """What Jev has cost so far: version 2's tokens in the ledger at TypeSafe's price."""
    with ledger.connect(dsn) as conn:
        d = G.decision(conn, NAME)
        tokens = conn.execute("SELECT coalesce(sum(tokens), 0) FROM docket.judgment WHERE decision_id = %s "
                              "AND version = %s", (d["id"], v2)).fetchone()[0]
    return float(tokens) * USD_PER_INPUT_TOKEN


def _cli(args):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        cli.main(args)
    return out.getvalue()


def label(dsn):
    """Label the drawn texts as a person reading them would: the product their complaint chose."""
    with ledger.connect(dsn) as conn:
        truth = dict(conn.execute("SELECT id::text, product FROM demo.cfpb_texts").fetchall())
    with open(SHEET, newline="") as f:
        sheet = list(csv.DictReader(f))
    if not sheet:
        return "nothing to label"
    for s in sheet:
        s["label"] = truth[s["key"]]
    with open(SHEET, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(sheet[0]))
        w.writeheader()
        w.writerows(sheet)
    return _cli(["label", "--dsn", dsn, NAME, "--csv", SHEET])


def rounds(dsn, v2, answer, target, cap, log):
    """`estimate --with --weight --target --queue --draw` round after round, as a user runs it, until the plan asks
    for nothing more. Returns each round's report and what the round did."""
    args = ["estimate", "--dsn", dsn, NAME, "--answer", answer, "--weight", WEIGHT, "--with", str(v2),
            "--target", str(target), "--label-cost", "1", "--queue", "--draw", SHEET]
    out = []
    for _ in range(ROUNDS):
        report = _cli(args)
        step = {"report": report, "jev_usd": None, "judged": 0, "labelled": 0}
        out.append(step)
        log(report)
        if "the target is met" in report:
            break
        if "rows queued for" in report:
            room = cap - spent(dsn, v2) - 0.002           # the worker stops one batch of 50 rows past its cap
            if room <= 0:
                log(f"Jev cap of ${cap:.2f} reached: stopping")
                break
            totals = Worker(dsn, api_key=os.environ.get("TYPESAFE_API_KEY") or None, concurrency=8, claim_size=50,
                            max_usd=room, poll=0.5, log=log).run(once=True)
            step["judged"] = totals["judged"]
        if "rows to label written" in report:
            log(label(dsn))
            with open(SHEET, newline="") as f:
                step["labelled"] = sum(1 for _ in csv.DictReader(f))
        step["jev_usd"] = spent(dsn, v2)
        if not (step["judged"] or step["labelled"]):
            break
    return out


def summary(dsn, v2, answer, target):
    """The estimate as the CLI gives it at the end of an answer's rounds, against the truth."""
    with ledger.connect(dsn) as conn:
        r = MW.estimate_with(conn, NAME, answer, [v2], WEIGHT, target=target, label_cost=1.0)
        truth, own = conn.execute(f"""
            SELECT sum({WEIGHT} * (s.product = %(a)s)::int)::float8 / sum({WEIGHT}),
                   (SELECT avg((product = %(a)s)::int) FROM demo.cfpb_complaints) FROM demo.cfpb_texts s""",
                                  {"a": answer}).fetchone()
    g, e = r["groups"][0], r["groups"][0]["est"]
    return {"answer": answer, "target": target, "truth": float(truth), "complaints_own_product": float(own),
            "laya_says": g["s"]["wa"] / g["s"]["weight"], "theta": e["theta"], "se": e["se"], "ci": e["ci"],
            "held": bool(e["ci"][0] <= truth <= e["ci"][1]), "reliable": e["reliable"], "used": e["used"],
            "labels": e["labels"], "drawn": e["drawn"], "certain": e["certain"], "exact_share": e["exact_share"],
            "jev_texts": g["s"]["vjudged"], "plan": g["plan"][0]["kind"] if g["plan"] else None, "render": MW.render(r)}


def data(dsn, v2):
    """Per text, in the order of the ranking by u / w: weight, Laya's answer, Jev's (None where it hasn't judged), the
    label the text's complaint chose, whether it is labelled."""
    with ledger.connect(dsn) as conn:
        d = G.decision(conn, NAME)
        rows = conn.execute(f"""
            SELECT s.id, {WEIGHT}, j1.value, j2.value, s.product, l.row_key IS NOT NULL FROM demo.cfpb_texts s
            JOIN docket.current c1 ON c1.decision_id = %(d)s AND c1.version = 1 AND c1.row_key = s.id::text
            JOIN docket.judgment j1 ON j1.id = c1.judgment_id
            LEFT JOIN docket.current c2 ON c2.decision_id = %(d)s AND c2.version = %(v)s AND c2.row_key = s.id::text
            LEFT JOIN docket.judgment j2 ON j2.id = c2.judgment_id
            LEFT JOIN docket.label l ON l.decision_id = %(d)s AND l.row_key = s.id::text""",
                            {"d": d["id"], "v": v2}).fetchall()
    w = np.array([x[1] for x in rows], float)
    u = np.array([G.prn(str(x[0])) for x in rows])
    order = np.argsort(u / w)
    pick = lambda i: [rows[k][i] for k in order]
    return {"w": w[order], "u": u[order], "laya": pick(2), "jev": pick(3), "truth": pick(4),
            "labelled": np.array(pick(5), bool)}


def prefixes(dat):
    """Whether the labelled texts, and the texts Jev judged, are each the first texts of the ranking: what the
    estimates assume, and what --draw and --queue should have made them."""
    jev = np.array([j is not None for j in dat["jev"]])
    first = lambda m: bool(m[:m.sum()].all())
    return first(dat["labelled"]) and first(jev)


def assess(dat, x):
    """A stored answer's state again (its labels and Jev's run are the first texts of the ranking): the estimate
    recomputed outside the database, and the one Laya alone gives from the same labels."""
    n0, n1 = x["labels"], x["jev_texts"]
    y = np.array([t == x["answer"] for t in dat["truth"]], float)
    a = np.array([t == x["answer"] for t in dat["laya"]], float)
    v = np.array([np.nan if t is None else float(t == x["answer"]) for t in dat["jev"]])
    rank = np.arange(len(y))
    lab = np.where(rank < n0, y, np.nan)
    both = MW.estimate(MW.sums(G.ALL, dat["w"], a, np.where(rank < n1, v, np.nan), lab, dat["u"]), Z)
    alone = MW.estimate(MW.sums(G.ALL, dat["w"], a, np.full(len(y), np.nan), lab, dat["u"]), Z)
    jev = rank < n1
    return {"recomputed": abs(both["theta"] - x["theta"]) < 1e-9 and abs(both["se"] - x["se"]) < 1e-9,
            "alone_half_width": Z * alone["se"], "alone_held": bool(alone["ci"][0] <= x["truth"] <= alone["ci"][1]),
            "jev_right": float((v[jev] == y[jev]).mean()) if jev.any() else None,
            "laya_right_there": float((a[jev] == y[jev]).mean()) if jev.any() else None}


def replay(dat, runs):
    """#26's plan with Laya alone, round after round on the same ranking, the answers in the order the live run took
    them and each starting from the labels the ones before left: simulated on the live answers, with no calls.
    Per answer: the labels at the end of its rounds, and the half-width there."""
    w, u = dat["w"], dat["u"]
    r = u / w
    x0, out = 0.0, []
    for x in runs:
        y = np.array([t == x["answer"] for t in dat["truth"]], float)
        a = np.array([t == x["answer"] for t in dat["laya"]], float)
        heavy = {"thr": 0.0, "rows": sorted(((float(wi), int(ai)) for wi, ai in zip(w, a)), reverse=True)}
        for done in range(ROUNDS + 1):
            s = MW.sums(G.ALL, w, a, np.full(len(w), np.nan), np.where(r < x0, y, np.nan), u)
            e = MW.estimate(s, Z)
            p = MW.plan(s, e, heavy, x["target"], Z, 1.0, 0.0, with_v=False)[0]
            if p["kind"] == "met" or done == ROUNDS:
                break
            x0 = max(x0, MW.this_round(p)["xi0"])
        out.append({"labels": int((r < x0).sum()), "rounds": done + 1, "half_width": Z * e["se"],
                    "held": bool(e["ci"][0] <= x["truth"] <= e["ci"][1])})
    return out


def agreement(dsn):
    """How often Laya's live answers equal its stored Phase 0 answers to the same texts (Laya is deterministic), on
    how many texts, and the seconds from its first judgment to its last."""
    with ledger.connect(dsn) as conn:
        d = G.decision(conn, NAME)
        same, n = conn.execute("""
            SELECT avg((j.value = r.answer->>'choice')::int), count(*)
            FROM demo.cfpb_texts s
            JOIN docket.current c ON c.decision_id = %s AND c.version = 1 AND c.row_key = s.id::text
            JOIN docket.judgment j ON j.id = c.judgment_id
            JOIN bench.results r ON r.text_hash = s.text_hash AND r.run_id = %s AND r.experiment = 'choice_base'""",
                               (d["id"], RUNS["Laya, 1 row per request"])).fetchone()
        took = conn.execute("SELECT extract(epoch FROM max(judged_at) - min(judged_at)) FROM docket.judgment "
                            "WHERE decision_id = %s AND version = 1", (d["id"],)).fetchone()[0]
    return float(same), int(n), float(took)


def run(target=0.02, max_usd=0.10, answers=ANSWERS, setup_only=False, report_only=False):
    dsn = os.environ["DATABASE_URL"]
    log = lambda *a: print(*a, flush=True)
    build(dsn, log)
    v2 = decide(dsn, log)
    same, n, took = agreement(dsn)
    log(f"Laya's live answers equal its stored ones on {100 * same:.1f} % of {n:,} texts")
    if setup_only:
        return None
    path = f"{OUT}/results.json"
    res = json.load(open(path)) if os.path.exists(path) else {"runs": []}
    res.update(laya_agreement=same, texts=n, laya_seconds=took)
    for answer in ([] if report_only else answers):
        steps = rounds(dsn, v2, answer, target, max_usd, log)
        res["runs"] = [x for x in res["runs"] if (x["answer"], x["target"]) != (answer, target)]
        res["runs"].append({**summary(dsn, v2, answer, target), "rounds": len(steps),
                            "steps": [{k: v for k, v in s.items() if k != "report"} for s in steps]})
    dat = data(dsn, v2)
    res["prefixes"] = prefixes(dat)
    for x, alone in zip(res["runs"], replay(dat, res["runs"])):
        x.update(assess(dat, x), laya_alone=alone)
    res["jev_usd"] = spent(dsn, v2)
    os.makedirs(OUT, exist_ok=True)
    with open(path, "w") as fh:
        json.dump(res, fh, indent=2, default=float)
    with open(f"{OUT}/report.md", "w") as fh:
        fh.write(render(res))
    return OUT


def render(res):
    pct = lambda x: "–" if x is None else f"{100 * x:.1f} %"
    pts = lambda x: f"±{100 * x:.2f}"
    runs = res["runs"]
    L = ["# Several models for a weighted share, live", "",
         "Generated by `jevbench livejoin`, with real calls: Laya (open weights, on this machine's CPU) judging every "
         f"one of the {res['texts']:,} CFPB complaint texts as the active version, and Jev 1.14 one row per request "
         "judging the runs of the ranking by weight the plans asked for. The texts stand for the 21,980 complaints in "
         "the source shard that carry them word for word; each is counted by "
         f"`{WEIGHT}`. Drawn texts were labelled with the product their complaint chose. Labels at $1. The answers ran "
         f"in the order below, each from the labels the ones before left. Jev cost ${res['jev_usd']:.4f} in all.", "",
         f"Laya's live answers equal its stored Phase 0 answers on {pct(res['laya_agreement'])} of the texts"
         + f"; it judged them in {res['laya_seconds'] / 60:.0f} minutes, {res['laya_seconds'] / res['texts']:.2f} s a text."
         + " The labelled texts and the texts Jev judged are each the first texts of the ranking by weight: "
         + ("checked." if res["prefixes"] else "**not so**.")
         + " Each estimate recomputed outside the database from the same texts: "
         + ("the same." if all(x["recomputed"] for x in runs) else "**different**."), "",
         "| share of complaints | target | truth | Laya says | estimate | 95 % interval | held | rounds | labels "
         "(certain) | weight exact | texts Jev judged |",
         "| --- | ---: | ---: | ---: | ---: | --- | --- | ---: | ---: | ---: | ---: |"]
    for x in runs:
        L.append(f"| {x['answer']} | ± {100 * x['target']:.0f} | {pct(x['truth'])} | {pct(x['laya_says'])} | "
                 f"{pct(x['theta'])} | {100 * x['ci'][0]:.1f} – {100 * x['ci'][1]:.1f} | {'yes' if x['held'] else 'no'} | "
                 f"{x['rounds']} | {x['labels']:,} ({x['certain']:,}) | {pct(x['exact_share'])} | {x['jev_texts']:,} |")
    L += ["", "**What Jev added.** The same labels with Laya alone (#26's estimate), and the labels #26's plan with Laya "
          "alone takes round after round on the same ranking, in the same order (simulated on the live answers, no "
          "calls). On the texts Jev judged, how often each model was right about the answer.", "",
          "| share of complaints | half-width with Jev | Laya alone, same labels | labels with Jev | labels, Laya "
          "alone's rounds | Jev right there | Laya right there |", "| --- | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for x in runs:
        a = x["laya_alone"]
        L.append(f"| {x['answer']} | {pts(Z * x['se'])} | {pts(x['alone_half_width'])} | {x['labels']:,} | "
                 f"{a['labels']:,} | {pct(x['jev_right'])} | {pct(x['laya_right_there'])} |")
    own = {x["answer"]: x["complaints_own_product"] for x in runs}
    L += ["", "Truth: the weighted share of the texts' labels. The complaints' own product field, which differs on a "
          "few copies, gives " + "; ".join(f"{a} {pct(p)}" for a, p in own.items()) + ".", ""]
    for x in runs:
        L += [f"## {x['answer'].capitalize()}, ± {100 * x['target']:.0f} points: the last round", "", "```",
              x["render"], "```", ""]
    return "\n".join(L)
