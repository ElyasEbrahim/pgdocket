"""docket: durable, versioned, calibrated AI decisions for Postgres.

  pgdocket check      how trustworthy is an AI decision on your table? (read-only)
  pgdocket report     re-analyse a check folder from its saved answers (no API calls)

  pgdocket install    create or upgrade the docket schema (no superuser needed)
  pgdocket create     declare a decision on a table: triggers, queue, view docket.v_<name>
  pgdocket worker     judge queued rows and keep the ledger current
  pgdocket label      import human labels (from a column or a key,label CSV)
  pgdocket calibrate  fit a calibrator and a guaranteed threshold from the labels
  pgdocket estimate   how many rows truly have each answer, with a confidence interval; --by: a share per group;
                      --weight: a share of a sum, or of a join's rows
  pgdocket plan       how much human review keeps the whole column's error ≤ ε, and the review queue
  pgdocket drift      have the rows or the model shifted since calibration? (rows: free; model: re-asks labelled rows)
  pgdocket audit      are the answers marked auto still right? blind spot checks with an anytime-valid alarm
  pgdocket status     decisions, queue depth, errors, labels, tokens
  pgdocket revise     add a shadow version (new question, options or model) without changing answers
  pgdocket compare    judge a sample under both versions; report what changed beyond run-to-run noise
  pgdocket backfill   queue every row for a version
  pgdocket promote    make a shadow version the one the view shows
  pgdocket retire     stand down a shadow version (it stops judging new rows)
  pgdocket versions   list a decision's versions
  pgdocket drop       remove a decision (triggers, view, ledger); the source table is untouched
"""
import argparse
import json
import os
import sys

from .check import CheckConfig, export_sample, reanalyze, run_check


def _options(args):
    opts = []
    if args.options_file:
        with open(args.options_file) as f:
            data = json.load(f)
        opts = [o if isinstance(o, dict) else {"name": o} for o in data]
    elif args.options:
        opts = [{"name": o.strip()} for o in args.options.split(",") if o.strip()]
    return opts


def main(argv=None):
    ap = argparse.ArgumentParser(prog="pgdocket", description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--version", action="version", version="pgdocket " + __import__("pgdocket").__version__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check", help="sample, judge and score an AI decision against labels (read-only)")
    c.add_argument("--dsn", default=os.environ.get("DATABASE_URL"), help="Postgres connection string (or DATABASE_URL)")
    c.add_argument("--table", required=True, help="schema.table")
    c.add_argument("--key", required=True, help="primary-key column")
    c.add_argument("--text", required=True, help="comma-separated columns sent to the model")
    c.add_argument("--kind", choices=["choice", "noul"], required=True, help="choice: pick one option; noul: yes/no")
    c.add_argument("--question", required=True, help="choice: the question; noul: the condition to test")
    c.add_argument("--options", help="choice: comma-separated option names (must match the label values)")
    c.add_argument("--options-file", help='choice: JSON list of {"name", "description"}')
    lab = c.add_mutually_exclusive_group()
    lab.add_argument("--label-column", help="column holding existing human labels")
    lab.add_argument("--label-file", help="CSV with key,label (see --export-sample)")
    c.add_argument("--positive", help="noul: the label value that means yes")
    c.add_argument("--where", help="extra SQL filter on the table, e.g. \"created_at > '2025-01-01'\"")
    c.add_argument("--sample", type=int, default=300)
    c.add_argument("--untrusted", action="store_true",
                   help="tell the model the rows come from third parties and to ignore instructions in them")
    c.add_argument("--steer", type=int, default=0, metavar="N",
                   help="also try to steer N rows with text appended by their author, and test erase-and-check "
                        "(about 20 extra calls per row)")
    c.add_argument("--seed", default="1")
    c.add_argument("--backend", default="jev", help="jev | sim | any /v1/systemone URL")
    c.add_argument("--model", default="jev-1.14.0")
    c.add_argument("--batch-size", type=int, default=1)
    c.add_argument("--repeat", type=int, default=0, help="re-judge this many rows to measure stability")
    c.add_argument("--batch-test", type=int, default=0, help="re-judge this many rows 20 per request")
    c.add_argument("--precision-target", type=float, default=0.9)
    c.add_argument("--max-usd", type=float, default=1.0)
    c.add_argument("--yes", action="store_true", help="confirm a paid run")
    c.add_argument("--out", help="output folder (default results/check-<timestamp>)")
    c.add_argument("--export-sample", metavar="CSV", help="only write the sample (with an empty label column) and exit")
    def dsn_arg(p):
        p.add_argument("--dsn", default=os.environ.get("DATABASE_URL"), help="Postgres connection string (or DATABASE_URL)")

    i = sub.add_parser("install", help="create or upgrade the docket schema")
    dsn_arg(i)
    cr = sub.add_parser("create", help="declare a decision on a table")
    dsn_arg(cr)
    cr.add_argument("name")
    cr.add_argument("--table", required=True)
    cr.add_argument("--key", required=True)
    cr.add_argument("--text", required=True, help="comma-separated columns sent to the model")
    cr.add_argument("--kind", choices=["choice", "noul"], required=True)
    cr.add_argument("--question", required=True)
    cr.add_argument("--options")
    cr.add_argument("--options-file")
    cr.add_argument("--backend", default="jev")
    cr.add_argument("--model", default="jev-1.14.0")
    cr.add_argument("--batch-size", type=int, default=1)
    cr.add_argument("--no-backfill", action="store_true", help="only judge rows inserted or changed from now on")
    cr.add_argument("--untrusted", action="store_true",
                    help="outsiders write the rows: tell the model to ignore instructions in them")
    w = sub.add_parser("worker", help="judge queued rows")
    dsn_arg(w)
    w.add_argument("--once", action="store_true", help="exit when the queue is empty")
    w.add_argument("--max-usd", type=float, default=None, help="stop after spending this much")
    w.add_argument("--claim-size", type=int, default=100)
    w.add_argument("--concurrency", type=int, default=16)
    w.add_argument("--poll", type=float, default=5.0, help="seconds between polls when idle")
    lb = sub.add_parser("label", help="import human labels")
    dsn_arg(lb)
    lb.add_argument("name")
    src = lb.add_mutually_exclusive_group(required=True)
    src.add_argument("--from-column")
    src.add_argument("--csv")
    lb.add_argument("--sample", type=int, default=None, help="with --from-column: a random sample of this many rows")
    lb.add_argument("--seed", default="1")
    lb.add_argument("--positive", help="yes/no decisions: the label value that means yes")
    lb.add_argument("--random", action="store_true",
                    help="with --csv: the rows were drawn at random (e.g. by pgdocket check --export-sample)")
    lb.add_argument("--stratified", metavar="COL",
                    help="with --csv: the rows were drawn at random within each value of COL (pgdocket estimate --by COL --draw)")
    ca = sub.add_parser("calibrate", help="fit a calibrator and a guaranteed threshold")
    dsn_arg(ca)
    ca.add_argument("name")
    ca.add_argument("--precision-target", type=float, default=0.9)
    ca.add_argument("--recall-target", type=float,
                    help="yes/no: also answer 'no' without review below a threshold that keeps this share of true yeses")
    ca.add_argument("--per-answer", action="store_true",
                    help="choice: one threshold and guarantee per answer (answers with too few labels get no auto)")
    ca.add_argument("--include-manual", action="store_true",
                    help="also fit on hand-picked labels (e.g. exported disagreements); may bias the guarantee")
    ca.add_argument("--harden", action="store_true",
                    help="auto only for rows that pass erase-and-check: no answer changes with any one piece of their "
                         "text removed (about 5 extra calls per accepted row)")
    es = sub.add_parser("estimate", help="counts with confidence intervals (prediction-powered inference)")
    dsn_arg(es)
    es.add_argument("name")
    es.add_argument("--answer", help="one option (or 'yes'); default: every option")
    es.add_argument("--where", help="SQL condition on the source table, e.g. \"created_at >= '2026-07-01'\"")
    es.add_argument("--level", type=float, default=0.95)
    es.add_argument("--include-manual", action="store_true", help="also use labels not drawn at random (intervals may be biased)")
    es.add_argument("--by", metavar="COL", help="a share per value of this source column, each group corrected with its own labels")
    es.add_argument("--weight", metavar="EXPR",
                    help="count each row by this SQL expression on the source row s: a column to sum, or how often a join "
                         "uses the row, e.g. \"(SELECT count(*) FROM orders o WHERE o.ticket_id = s.id)\"")
    es.add_argument("--target", type=float, metavar="SHARE",
                    help="with --by or --weight: how many more labels each group needs for an interval of ± SHARE (e.g. 0.02)")
    es.add_argument("--draw", metavar="CSV",
                    help="with --target: write those rows to label (drawn at random within each group; with --weight, by weight)")
    es.add_argument("--seed", default="1",
                    help="with --draw; with --weight, also the ranking rows are drawn by, so keep it for later estimates")
    pl = sub.add_parser("plan", help="how much review keeps the column's error ≤ ε (95 %% confidence)")
    dsn_arg(pl)
    pl.add_argument("name")
    pl.add_argument("--max-error", type=float, default=0.05, help="share of judged rows allowed to stay wrong")
    pl.add_argument("--export", metavar="CSV", help="write the review queue: rows below the cut, least confident first")
    dt = sub.add_parser("drift", help="have the rows or the model shifted? (exit status 1 on an alarm)")
    dsn_arg(dt)
    dt.add_argument("name")
    dt.add_argument("--days", type=float, default=7, help="recent rows (the last DAYS days) against all earlier ones")
    dt.add_argument("--by", metavar="COLUMN",
                    help="a timestamp column of the source table that dates each row (default: when it was judged)")
    dt.add_argument("--anchors", type=int, default=0, metavar="N",
                    help="also re-ask the active version on N labelled rows to check the model (N API calls)")
    dt.add_argument("--model", action="store_true", help="check the model on anchors already asked again")
    dt.add_argument("--seed", default="1", help="picks the anchors; the same seed gives the same panel each time")
    dt.add_argument("--run", action="store_true", help="with --anchors: run the worker until they are answered")
    dt.add_argument("--wait", type=float, default=0, metavar="SECONDS",
                    help="with --anchors: wait up to this long for a running worker to answer them")
    dt.add_argument("--max-usd", type=float, default=1.0, help="with --run: stop after spending this much")
    au = sub.add_parser("audit", help="are the answers marked auto still right? (exit status 1 on an alarm)")
    dsn_arg(au)
    au.add_argument("name")
    au.add_argument("--draw", type=int, metavar="N",
                    help="draw N rows at random from those marked auto since the last batch, to label")
    au.add_argument("--out", metavar="CSV", help="with --draw: write them to a CSV to label, without the AI's answers")
    au.add_argument("--from-column", metavar="COLUMN", help="with --draw: take their labels from this column of the table")
    au.add_argument("--positive", help="yes/no decisions with --from-column: the label value that means yes")
    au.add_argument("--seed", default="1")
    st = sub.add_parser("status", help="decisions, queue, errors, labels, tokens")
    dsn_arg(st)
    rv = sub.add_parser("revise", help="add a shadow version; the view keeps showing the active one")
    dsn_arg(rv)
    rv.add_argument("name")
    rv.add_argument("--question")
    rv.add_argument("--options", help="choice: comma-separated option names (replaces the options)")
    rv.add_argument("--options-file", help='choice: JSON list of {"name", "description"}')
    rv.add_argument("--model")
    rv.add_argument("--backend")
    rv.add_argument("--batch-size", type=int)
    rv.add_argument("--backfill", action="store_true", help="queue every row now (default: compare a sample first)")
    rv.add_argument("--untrusted", action=argparse.BooleanOptionalAction, default=None,
                    help="tell the model to ignore instructions in the rows (or --no-untrusted to stop)")
    cp = sub.add_parser("compare", help="compare a shadow version with the active one on a sample")
    dsn_arg(cp)
    cp.add_argument("name")
    cp.add_argument("version", type=int)
    cp.add_argument("--sample", type=int, default=0,
                    help="queue this many rows (labelled ones first) for both versions; 0: report on what is judged")
    cp.add_argument("--seed", default="1")
    cp.add_argument("--run", action="store_true", help="run the worker until the queue is empty, then report "
                                                         "(when no worker is running)")
    cp.add_argument("--wait", type=float, default=0, metavar="SECONDS",
                    help="wait up to this long for a running worker to finish the comparison, then report")
    cp.add_argument("--max-usd", type=float, default=1.0, help="with --run: stop after spending this much")
    cp.add_argument("--out", help="also write report.md and report.json to this folder")
    cp.add_argument("--precision-target", type=float,
                    help="for the auto-accept comparison (default: the active version's, or 0.9)")
    cp.add_argument("--export-disagreements", metavar="CSV",
                    help="write the unlabelled rows where the versions disagree, to label and import with `label --csv`")
    bf = sub.add_parser("backfill", help="queue every row for a version")
    dsn_arg(bf)
    bf.add_argument("name")
    bf.add_argument("--version", type=int, help="default: every version that isn't retired")
    pr = sub.add_parser("promote", help="make a shadow version active")
    dsn_arg(pr)
    pr.add_argument("name")
    pr.add_argument("version", type=int)
    pr.add_argument("--force", action="store_true", help="promote even if some rows have no answer from it yet")
    rt = sub.add_parser("retire", help="stand down a shadow version; its judgments stay in the ledger")
    dsn_arg(rt)
    rt.add_argument("name")
    rt.add_argument("version", type=int)
    vs = sub.add_parser("versions", help="list a decision's versions")
    dsn_arg(vs)
    vs.add_argument("name")
    dr = sub.add_parser("drop", help="remove a decision and its ledger")
    dsn_arg(dr)
    dr.add_argument("name")
    dr.add_argument("--yes", action="store_true", help="confirm: this deletes the decision's ledger")

    r = sub.add_parser("report", help="re-analyse a check folder from its saved answers (no API calls)")
    r.add_argument("folder")
    r.add_argument("--precision-target", type=float, default=None)
    a = ap.parse_args(argv)

    if a.cmd == "report":
        reanalyze(a.folder, a.precision_target)
        print(f"report: {a.folder}/report.md")
        return
    if a.cmd in ("install", "create", "worker", "label", "calibrate", "estimate", "plan", "drift", "audit", "status", "drop",
                 "revise", "compare", "backfill", "promote", "retire", "versions"):
        if not a.dsn:
            sys.exit("no database: pass --dsn or set DATABASE_URL")
        return _ledger_command(a)
    if not a.dsn:
        sys.exit("no database: pass --dsn or set DATABASE_URL")
    cfg = CheckConfig(dsn=a.dsn, table=a.table, key=a.key, text=[t.strip() for t in a.text.split(",")], kind=a.kind,
                      question=a.question, options=_options(a), label_column=a.label_column, label_file=a.label_file,
                      positive=a.positive, where=a.where, sample=a.sample, seed=a.seed, backend=a.backend,
                      model=a.model, batch_size=a.batch_size, repeat=a.repeat, batch_test=a.batch_test, steer=a.steer,
                      untrusted=a.untrusted,
                      precision_target=a.precision_target, max_usd=a.max_usd, out=a.out)
    if a.export_sample:
        n = export_sample(cfg, a.export_sample)
        print(f"wrote {n} rows to {a.export_sample}; fill in the label column, then run with --label-file")
        return
    if a.kind == "choice" and not cfg.options:
        sys.exit("choice needs --options or --options-file")
    if a.kind == "noul" and not a.positive:
        sys.exit("noul needs --positive (the label value that means yes)")
    if not (a.label_column or a.label_file):
        sys.exit("needs labels: --label-column, or --label-file (create one with --export-sample)")
    out, _ = run_check(cfg, api_key=os.environ.get("TYPESAFE_API_KEY") or None, yes=a.yes)
    print(f"report: {out}/report.md")


def _ledger_command(a):
    from . import ledger
    if a.cmd == "install":
        applied = ledger.install(a.dsn)
        print("applied: " + (", ".join(applied) if applied else "nothing (already up to date)"))
    elif a.cmd == "create":
        spec = ledger.load_spec(a.kind, a.question, a.options, a.options_file, a.backend, a.model, a.batch_size,
                                a.untrusted)
        did, queued = ledger.create(a.dsn, a.name, a.table, a.key, [t.strip() for t in a.text.split(",")], spec,
                                    backfill=not a.no_backfill)
        print(f"decision {a.name} (id {did}) created; {queued} rows queued; read it from docket.v_{a.name}")
    elif a.cmd == "worker":
        from .worker import Worker
        totals = Worker(a.dsn, api_key=os.environ.get("TYPESAFE_API_KEY") or None, claim_size=a.claim_size,
                        concurrency=a.concurrency, max_usd=a.max_usd, poll=a.poll).run(once=a.once)
        print(json.dumps(totals))
    elif a.cmd == "label":
        if a.stratified and (a.from_column or a.random):
            sys.exit("--stratified goes with --csv, for a file from pgdocket estimate --by COL --draw")
        n = ledger.import_labels(a.dsn, a.name, column=a.from_column, csv_path=a.csv, sample=a.sample, seed=a.seed,
                                 positive=a.positive, random=a.random, stratified=a.stratified)
        kind = (f"drawn within {a.stratified} groups" if a.stratified else "random sample" if (a.sample or a.random)
                else ("whole column" if a.from_column else "manual"))
        print(f"{n} labels imported for {a.name} ({kind})")
    elif a.cmd == "calibrate":
        try:
            cal = ledger.calibrate(a.dsn, a.name, a.precision_target, include_manual=a.include_manual,
                                   per_answer=a.per_answer, recall_target=a.recall_target, harden=a.harden)
        except ValueError as e:                       # too few labelled rows with an answer: say why, no traceback
            if "labeled rows" not in str(e):
                sys.exit(f"{a.name}: {e}")
            queued = next((r["queued"] for r in ledger.status(a.dsn) if r["name"] == a.name), 0)
            hint = (f" {queued:,} rows are still queued: wait for the worker to answer them (`pgdocket status` shows the "
                    "queue), then run calibrate again." if queued else " Label more rows with `pgdocket label`.")
            sys.exit(f"{a.name}: can't calibrate yet: {e}.{hint}")
        print(f"{a.name}: {cal['n_labels']} labels · calibration error {cal['ece_raw']:.3f} → {cal['ece_calibrated']:.3f} · "
              + _threshold_summary(cal))
        for line in filter(None, [_recall_summary(cal), *_per_answer_notes(cal, a.name)]):
            print(line)
        if a.harden:
            print(f"  hardened: {cal['checks_queued']:,} accepted rows queued for erase-and-check; until the worker has "
                  "checked a row, `auto` is false for it")
    elif a.cmd == "estimate" and (a.by or a.weight):
        _estimate_by(a, ledger)
    elif a.cmd == "estimate":
        if a.target or a.draw:
            sys.exit("--target and --draw plan labels for a share: add --by COL or --weight EXPR")
        rows = ledger.estimate(a.dsn, a.name, a.answer, a.where, a.level, a.include_manual)
        lvl = f"{100 * a.level:.0f} %"
        print(f"{a.name}: how many judged rows truly have each answer ({lvl} intervals)"
              + (f", where {a.where}" if a.where else ""))
        print(f"{'answer':<22} {'model says':>10}  {'estimate':>9}  {lvl + ' interval':>20}  {'labels only':>20}  {'labels':>9}")
        for r in rows:
            ci = lambda lo, hi: "–" if lo is None else f"{lo:,.0f} – {hi:,.0f}"
            est = "–" if r["estimate"] is None else f"{r['estimate']:,.0f}"
            flag = "" if r["reliable"] else "  ⚠"
            print(f"{r['answer']:<22} {r['model_count']:>10,.0f}  {est:>9}  {ci(r['ci_low'], r['ci_high']):>20}  "
                  f"{ci(r['labels_only_low'], r['labels_only_high']):>20}  {r['labels_with']:>3}/{r['labels']:<5}{flag}")
        if rows:
            print(f"judged rows in scope: {rows[0]['judged']:,} of {rows[0]['rows_in_scope']:,}")
        for r in rows:
            if r["note"]:
                print(f"  {r['answer']}: {r['note']}")
        if any(not r["reliable"] for r in rows):
            print("⚠ fewer than 10 labelled rows with or without that answer: label more rows before relying on it")
    elif a.cmd == "plan":
        _plan(a, ledger)
    elif a.cmd == "drift":
        _drift(a, ledger)
    elif a.cmd == "audit":
        _audit(a, ledger)
    elif a.cmd == "drop":
        if not a.yes:
            sys.exit(f"this deletes decision {a.name} and its whole ledger; re-run with --yes")
        with ledger.connect(a.dsn) as conn:
            conn.execute("SELECT docket.drop_decision(%s)", (a.name,))
        print(f"decision {a.name} dropped")
    elif a.cmd == "revise":
        ver, spec = ledger.revise(a.dsn, a.name, a.question, a.options, a.options_file, a.model, a.backend, a.batch_size,
                                  a.backfill, a.untrusted)
        print(f"{a.name} v{ver} added as a shadow version; docket.v_{a.name} still shows the active one")
        print(f"next: pgdocket compare {a.name} {ver} --sample 200 --wait 600   (or --run if no worker is running)"
              if not a.backfill else
              f"every row is queued for v{ver}; when the worker is done: pgdocket compare {a.name} {ver} --sample 200")
    elif a.cmd == "compare":
        _compare(a, ledger)
    elif a.cmd == "backfill":
        n = ledger.backfill(a.dsn, a.name, a.version)
        print(f"{n:,} rows queued")
    elif a.cmd == "promote":
        r = ledger.promote(a.dsn, a.name, a.version, a.force)
        verdict = (r["comparison"] or {}).get("verdict")
        print(f"{a.name}: v{a.version} is active; v{r['previous']} is retired"
              + (f" (last comparison: {verdict})" if verdict else " (never compared)"))
        if r["missing"]:
            print(f"{r['missing']:,} rows show as pending until the worker judges them under v{a.version}")
        cal = r["calibration"]
        if cal:
            print(f"recalibrated v{a.version} on {cal['n_labels']} labels: calibration error {cal['ece_raw']:.3f} → "
                  f"{cal['ece_calibrated']:.3f} · " + _threshold_summary(cal))
            for line in filter(None, [_recall_summary(cal), *_per_answer_notes(cal, a.name)]):
                print(line)
        else:
            print(f"v{a.version} is not calibrated ({r['calibration_missing']}): `auto` is empty until you label rows "
                  f"and run `pgdocket calibrate {a.name}`")
    elif a.cmd == "retire":
        ledger.retire(a.dsn, a.name, a.version)
        print(f"{a.name} v{a.version} retired: it no longer judges new or changed rows")
    elif a.cmd == "versions":
        for v in ledger.versions(a.dsn, a.name):
            spec = v["spec"]
            what = f"{spec['model']}, {len(spec.get('options', []))} options" if spec["kind"] == "choice" else spec["model"]
            print(f"v{v['version']:<3} {v['status']:<8} judged {v['judged']:>8,}  {'calibrated' if v['calibrated'] else '':<10}  "
                  f"{v['verdict'] or '':<20} {what}  · created {v['created_at']:%Y-%m-%d %H:%M}"
                  + (f", promoted {v['promoted_at']:%Y-%m-%d %H:%M}" if v["promoted_at"] else ""))
    elif a.cmd == "status":
        rows = ledger.status(a.dsn)
        if not rows:
            print("no decisions yet")
        for r in rows:
            print(f"{r['name']:<24} v{r['version']}  judged {r['judged']:>8,}  queued {r['queued']:>6,}  errors {r['errors']:>4,}  "
                  f"labels {r['labels']:>5,}  tokens {r['tokens']:>12,}  {'calibrated' if r['calibrated'] else 'not calibrated'}")


def _estimate_by(a, ledger):
    from . import groups
    if a.draw and not a.target:
        sys.exit("--draw writes the labels a --target needs: add --target")
    if a.include_manual:
        sys.exit("--by and --weight use only labels drawn at random, within groups or by weight")
    import psycopg
    with ledger.connect(a.dsn) as conn:
        try:
            r = groups.estimate_by(conn, a.name, a.by, a.answer, a.where, a.level, a.target, a.weight, a.seed)
        except psycopg.Error as e:
            sys.exit(f"{a.name}: {str(e).splitlines()[0]} (--by and --where name columns of the source table; --weight "
                     "is an SQL expression on its row, s)")
        print(groups.render(r))
        if a.draw:
            d, rows = groups.draw(conn, a.name, a.by, r["plan"], a.where, a.seed)
            n = groups.export(conn, d, a.by, rows, a.draw)
            then = (f"  pgdocket label {a.name} --csv {a.draw}" + ("" if a.weight else f" --stratified {a.by}")
                    + ("\nand estimate with the same --seed" if a.weight and a.seed != "1" else ""))
            print(f"{n} rows to label written to {a.draw}, blind (no AI answer). Fill in `label`, then:\n{then}")


def _plan(a, ledger):
    from . import plan
    with ledger.connect(a.dsn) as conn:
        data = plan.fetch(conn, a.name)
        p = plan.from_rows(data["rows"], a.max_error)
        pct = lambda x: f"{100 * x:.1f} %"
        if not p["labels"]:
            sys.exit(f"{a.name}: no random-sample labels on judged rows; import some with "
                     f"`pgdocket label {a.name} --from-column … --sample 200` (or a CSV with --random)")
        print(f"{a.name}: {p['rows']:,} judged rows · {p['labels']} random labels · the AI alone: {pct(p['ai_error'])} wrong "
              f"(at most {pct(p['ai_error_upper'])} with 95 % confidence)")
        print(f"{'at most wrong':>14}  {'people review':>15}  {'the AI keeps':>12}")
        for t in p["targets"]:
            mark = "  ←" if t["requested"] else ""
            if t["review"] is None:
                print(f"{pct(t['max_error']):>14}  {'not provable':>15}  needs ≥ {t['labels_needed']} random labels{mark}")
            else:
                print(f"{pct(t['max_error']):>14}  {t['review']:>7,} ({pct(t['review_share']):>6})  "
                      f"{p['rows'] - t['review']:>12,}{mark}")
        chosen = next(t for t in p["targets"] if t["requested"])
        if chosen["review"] is None:
            print(f"with {p['labels']} random labels, no plan can show ≤ {pct(a.max_error)}: label at least "
                  f"{chosen['labels_needed']} at random")
            return
        if chosen["review_all"]:
            print(f"to keep ≤ {pct(a.max_error)} wrong, people must review every row: the labels can't yet vouch for any "
                  "of the model's answers at this level")
        elif chosen["review"] == 0:
            print(f"the model's answers already keep the column ≤ {pct(a.max_error)} wrong (95 % confidence): no review needed")
        else:
            print(f"to keep ≤ {pct(a.max_error)} wrong (95 % confidence), people review the {chosen['review']:,} rows whose "
                  f"confidence is below {chosen['cut']:.3f}; their corrected answers replace the model's")
        if a.export and chosen["review"]:
            k = plan.export_queue(conn, a.name, data["rows"], chosen["cut"] if chosen["cut"] is not None else float("inf"),
                                  a.export)
            print(f"wrote {k:,} rows to {a.export} (least confident first; rows with a random label are already known)")


def _drift(a, ledger):
    import time
    from . import drift
    since = None
    if a.anchors:
        n, since = ledger.drift_enqueue(a.dsn, a.name, a.anchors, a.seed)
        print(f"{n} labelled rows queued to be asked again" if n else
              f"{a.name}: no labelled rows to ask again; import labels with `pgdocket label {a.name} … --sample 200`")
        if a.run:
            from .worker import Worker
            totals = Worker(a.dsn, api_key=os.environ.get("TYPESAFE_API_KEY") or None, max_usd=a.max_usd).run(once=True)
            print(f"worker: {totals['probed']} asked again, ${totals['usd']:.4f}")
        elif a.wait:
            deadline = time.monotonic() + a.wait
            with ledger.connect(a.dsn, autocommit=True) as conn:
                d = drift._decision(conn, a.name)
                while (left := drift.pending(conn, d)) and time.monotonic() < deadline:
                    time.sleep(1)
            print(f"waited: {left} still queued" if left else "waited: all answered")
        elif n:
            print(f"when the worker has answered them: pgdocket drift {a.name} --model")
    alarm = False
    with ledger.connect(a.dsn) as conn:
        _, r = drift.data_check(conn, a.name, a.days, a.by)
        alarm |= bool(r.get("alarm"))
        lines = drift.verdict_data(r)
        if a.model or (a.anchors and (a.run or a.wait)):
            _, r = drift.model_check(conn, a.name, since)
            alarm |= bool(r.get("alarm"))
            lines += drift.verdict_model(r)
    print("\n".join(lines))
    if alarm:
        sys.exit(1)


def _audit(a, ledger):
    from . import audit
    if (a.out or a.from_column) and not a.draw:
        sys.exit("--out and --from-column go with --draw N")
    if a.out and a.from_column:
        sys.exit("choose one: --out (people label a CSV) or --from-column (the labels are already in the table)")
    with ledger.connect(a.dsn) as conn:
        if a.draw:
            batch, keys, pool = audit.draw(conn, a.name, a.draw, a.seed)
            if keys:
                print(f"batch {batch}: {len(keys)} rows drawn at random from {pool:,} accepted rows not audited or labelled yet")
            if a.out and keys:
                audit.export(conn, a.name, keys, a.out)
                print(f"wrote {len(keys)} rows to {a.out}, without the AI's answers: fill in the label column, then "
                      f"`pgdocket label {a.name} --csv {a.out}` and `pgdocket audit {a.name}`")
            elif a.from_column and keys:
                n = audit.label_from_column(conn, a.name, keys, a.from_column, a.positive)
                print(f"{n} audit labels taken from column {a.from_column}")
            elif not keys:
                print("nothing to draw: no row has been marked auto since the last batch, or all were labelled")
        d, rows = audit.fetch(conn, a.name)
    r = audit.analyse(rows, d["calibration"]["precision_target"])
    print("\n".join(audit.render(a.name, d, r)))
    if r["alarm"]:
        sys.exit(1)


def _threshold_summary(cal):
    tgt = f"{100 * cal['precision_target']:.0f} %"
    if cal.get("mode") == "per answer":
        ok = sum(t is not None for t in cal["thresholds"].values())
        return (f"per answer: {ok} of {len(cal['thresholds'])} answers certified at ≥ {tgt} precision (95 % confidence "
                f"each); {100 * cal['coverage']:.1f} % of labelled rows auto")
    if cal["threshold"] is None:
        return f"no threshold reaches {tgt} precision with {cal['n_labels']} labels: `auto` is false everywhere"
    return (f"auto when the model's score ≥ {cal['threshold']:.3f}: {100 * cal['coverage']:.1f} % of labelled rows, "
            f"precision ≥ {tgt} (95 % confidence)")


def _recall_summary(cal):
    """Yes/no decisions with a recall target: the auto-no band, or why there is none."""
    if cal.get("recall_target") is None:
        return None
    tgt = f"{100 * cal['recall_target']:.0f} %"
    if cal["recall_threshold"] is None:
        return (f"  no 'no' answer can be automatic yet: keeping ≥ {tgt} of true yeses needs at least "
                f"{cal['positives_needed']} labelled yeses (have {cal['positives']})")
    return (f"  auto 'no' when P(yes) < {cal['recall_threshold']:.3f}: {100 * cal['dismissed']:.1f} % of labelled rows, "
            f"keeping ≥ {tgt} of true yeses (95 % confidence; {cal['positives']} labelled yeses)")


def _per_answer_notes(cal, name):
    """Answers whose auto-accepted rows are demonstrably below the target, and what --per-answer would allow."""
    tgt = f"{100 * cal['precision_target']:.0f} %"
    out = [f"  ⚠ {r['answer']}: {r['right']} of {r['auto']} auto-accepted answers right ({100 * r['precision']:.1f} %, at most "
           f"{100 * r['upper']:.1f} % with 95 % confidence), below {tgt}" for r in cal.get("per_answer", []) if r["below_target"]]
    if out and cal.get("mode") != "per answer":
        out.append(f"  the overall guarantee holds, but not for these answers: label more of their rows, or run "
                   f"`pgdocket calibrate {name} --per-answer` ({100 * cal['per_answer_coverage']:.1f} % of labelled rows "
                   "would be auto, each answer with its own guarantee)")
    return out


def _compare(a, ledger):
    from . import compare
    if a.sample:
        n = ledger.compare_enqueue(a.dsn, a.name, a.version, a.sample, a.seed)
        print(f"{n} rows queued for v{a.version} and for a re-ask of the active version")
    if a.run:
        from .worker import Worker
        totals = Worker(a.dsn, api_key=os.environ.get("TYPESAFE_API_KEY") or None, max_usd=a.max_usd).run(once=True)
        print(f"worker: {totals['judged']} judged, {totals['probed']} re-asked, ${totals['usd']:.4f}")
    if a.wait:
        _wait_for_comparison(a, ledger, compare)
    with ledger.connect(a.dsn) as conn:
        data = compare.fetch(conn, a.name, a.version)
        target = a.precision_target or (data["calibration"] or {}).get("precision_target", 0.9)
        result = compare.analyse(data["spec_old"], data["spec_new"], data["rows"], precision_target=target,
                                 per_answer=(data["calibration"] or {}).get("mode") == "per answer")
        if data["pending"]:
            result.setdefault("reasons", []).insert(0, f"{data['pending']} queued judgments are not done yet; "
                                                       "this report covers only finished rows.")
        if result["n"]:
            compare.store(conn, data["decision_id"], a.version, result)
        if a.export_disagreements:
            k = compare.export_disagreements(conn, data, a.export_disagreements)
            print(f"wrote {k} rows to {a.export_disagreements}; fill in the label column, then "
                  f"`pgdocket label {a.name} --csv {a.export_disagreements}` and compare again")
    md = compare.render(a.name, data, result) if result["n"] else None
    if a.out and md:
        print(f"report: {compare.write(a.out, md, result)}")
    print(md if md else "\n".join(result["reasons"]))


def _wait_for_comparison(a, ledger, compare):
    import time
    deadline = time.monotonic() + a.wait
    with ledger.connect(a.dsn, autocommit=True) as conn:
        d = conn.execute("SELECT id, active_version FROM docket.decision WHERE name = %s", (a.name,)).fetchone()
        if not d:
            sys.exit(f"no decision named {a.name!r}")
        while (left := compare.pending_count(conn, d[0], d[1], a.version)) and time.monotonic() < deadline:
            time.sleep(1)
    print(f"waited: {left} comparison judgments still queued" if left else "waited: comparison judged")


if __name__ == "__main__":
    main()
