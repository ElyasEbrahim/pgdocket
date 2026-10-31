"""Install the ledger schema and manage decisions, labels and calibration (ADR 0004)."""
import csv
import json
import os
from importlib import resources

from psycopg import sql
from psycopg.types.json import Jsonb

from . import calibration
from .spec import validate

MIGRATION_TABLE = "CREATE SCHEMA IF NOT EXISTS docket; " \
                  "CREATE TABLE IF NOT EXISTS docket.migration (version text PRIMARY KEY, " \
                  "applied_at timestamptz NOT NULL DEFAULT now())"


def connect(dsn, **kw):
    import psycopg
    return psycopg.connect(dsn, **kw)


def _migrations():
    files = sorted(f for f in resources.files("pgdocket").joinpath("sql").iterdir() if f.name.endswith(".sql"))
    return [(f.name.split("_", 1)[0], f.read_text()) for f in files]


def install(dsn):
    """Apply pending migrations in order; each in its own transaction. Returns the versions applied."""
    applied = []
    with connect(dsn) as conn:
        conn.execute(MIGRATION_TABLE)
        conn.commit()
        done = {r[0] for r in conn.execute("SELECT version FROM docket.migration")}
        for version, text in _migrations():
            if version in done:
                continue
            with conn.transaction():
                conn.execute(text)
                conn.execute("INSERT INTO docket.migration (version) VALUES (%s)", (version,))
            applied.append(version)
    return applied


def create(dsn, name, table, key, text, spec, backfill=True):
    validate(spec)
    with connect(dsn) as conn:
        did = conn.execute("SELECT docket.create_decision(%s, %s::regclass, %s, %s, %s, %s)",
                           (name, table, key, text, Jsonb(spec), backfill)).fetchone()[0]
        queued = conn.execute("SELECT count(*) FROM docket.queue WHERE decision_id = %s", (did,)).fetchone()[0]
    return did, queued


def _decision(conn, name):
    r = conn.execute("SELECT d.id, d.source, d.key_column, d.active_version, v.spec FROM docket.decision d "
                     "JOIN docket.version v ON v.decision_id = d.id AND v.version = d.active_version "
                     "WHERE d.name = %s", (name,)).fetchone()
    if not r:
        raise SystemExit(f"no decision named {name!r}")
    return {"id": r[0], "source": r[1], "key": r[2], "version": r[3], "spec": r[4]}


def import_labels(dsn, name, column=None, csv_path=None, sample=None, seed="1", positive=None, random=False,
                  stratified=None):
    """Copy human labels into docket.label, from a column of the source table or from a key,label CSV.

    Labels record how they were chosen: `--sample N` from a column is a uniform random sample ('random');
    a whole column is 'all'; a CSV is 'manual' unless the caller says it was drawn at random (`random=True`,
    e.g. a file from `pgdocket check --export-sample`). Rows an audit drew get 'audit' (#23): they are used only by
    `pgdocket audit`. Only 'random' labels feed `estimate` by default. A CSV from `estimate --by COL --draw` is
    'stratified' (`stratified=COL`, #25): drawn at random within COL's groups, it counts for `estimate --by COL` only.
    Rows the CSV marks drawn "by weight" (`estimate --weight --draw`, #26) are 'weighted': a sample of the query, not of
    the table, which `estimate --weight` finds again by their ranking.
    Sampling records how a row was chosen, so re-labelling a row drawn at random keeps it 'random', and one drawn within
    groups keeps it 'stratified' (docket.label_sampling).
    Labels may name an option of any version not yet retired (e.g. one only a shadow version has).
    For yes/no decisions, labels are stored as 'yes' / 'no' (`positive` is the value that means yes).
    """
    with connect(dsn) as conn:
        d = _decision(conn, name)
        noul = d["spec"]["kind"] == "noul"
        if noul and positive is None:
            raise SystemExit("a yes/no decision needs --positive: the label value that means yes")
        names = None if noul else [r[0] for r in conn.execute(           # options of any version still in use
            "SELECT DISTINCT o->>'name' FROM docket.version v, jsonb_array_elements(v.spec->'options') o "
            "WHERE v.decision_id = %s AND v.status <> 'retired'", (d["id"],))]
        if column:
            col = sql.Identifier(column)
            cond = sql.SQL(" AND {}::text = ANY(%(names)s)").format(col) if names else sql.SQL("")
            value = (sql.SQL("CASE WHEN s.{}::text = %(pos)s THEN 'yes' ELSE 'no' END").format(col) if noul
                     else sql.SQL("s.{}::text").format(col))
            q = sql.SQL("""
                INSERT INTO docket.label (decision_id, row_key, label, source, sampling)
                SELECT %(did)s, s.{key}::text, {value}, %(src)s, %(sampling)s FROM {table} s
                WHERE s.{col} IS NOT NULL{cond}
                ORDER BY md5(s.{key}::text || %(seed)s) {limit}
                ON CONFLICT (decision_id, row_key) DO UPDATE SET label = EXCLUDED.label, source = EXCLUDED.source,
                    (sampling, strata) = (SELECT x.sampling, x.strata FROM docket.label_sampling(
                        docket.label.sampling, docket.label.strata, EXCLUDED.sampling, EXCLUDED.strata) x),
                    labeled_at = now()""").format(
                key=sql.Identifier(d["key"]), col=col, value=value, table=sql.SQL(d["source"]), cond=cond,
                limit=sql.SQL("LIMIT {}").format(sql.Literal(int(sample))) if sample else sql.SQL(""))
            cur = conn.execute(q, {"did": d["id"], "src": f"column:{column}", "seed": ":" + str(seed), "names": names,
                                   "pos": None if positive is None else str(positive),
                                   "sampling": "random" if sample else "all"})
            return cur.rowcount
        audited = {r[0] for r in conn.execute(                      # rows an audit drew (#23): their labels stay apart
            "SELECT row_key FROM docket.audit WHERE decision_id = %s", (d["id"],))}
        rows = []
        with open(csv_path, newline="") as f:
            for r in csv.DictReader(f):
                label = (r.get("label") or "").strip()
                if not label:
                    continue
                if noul:
                    label = "yes" if label == str(positive) else "no"
                elif label not in names:
                    continue
                drawn = (r.get("drawn") or "").strip()
                kind = ("weighted" if drawn == "by weight" else "stratified" if stratified else
                        "audit" if r["key"] in audited else "random" if random else "manual")
                rows.append((d["id"], r["key"], label, "csv:" + os.path.basename(csv_path), kind,
                             [stratified] if kind == "stratified" else None))
        with conn.cursor() as cur:
            cur.executemany("INSERT INTO docket.label (decision_id, row_key, label, source, sampling, strata) "
                            "VALUES (%s, %s, %s, %s, %s, %s) "
                            "ON CONFLICT (decision_id, row_key) DO UPDATE SET label = EXCLUDED.label, source = EXCLUDED.source, "
                            "(sampling, strata) = (SELECT x.sampling, x.strata FROM docket.label_sampling("
                            "docket.label.sampling, docket.label.strata, EXCLUDED.sampling, EXCLUDED.strata) x), "
                            "labeled_at = now()", rows)
        return len(rows)


def calibrate(dsn, name, precision_target=0.9, alpha=0.05, include_manual=False, per_answer=False, recall_target=None,
              harden=False):
    """Fit a calibrator on labeled rows that have a current answer, and store it on the active version.

    Uses labels drawn at random or a whole column. Hand-picked labels ('manual', e.g. the disagreements exported by
    `compare`) are skipped unless `include_manual`: rows chosen for being hard would bias the calibrator, and the
    threshold guarantee assumes the labelled rows are like the rest.
    `per_answer`: one threshold and guarantee per answer instead of one for all accepted rows together (#19).
    `recall_target` (yes/no decisions): also a threshold below which rows are answered "no" without review (#21).
    `harden`: a row is `auto` only once erase-and-check has passed it (#24); the rows `auto` accepts now are queued
    for the worker to check (`cal["checks_queued"]`).
    """
    with connect(dsn) as conn:
        return _calibrate(conn, _decision(conn, name), precision_target, alpha, include_manual, per_answer, recall_target,
                          harden)


def harden_enqueue(conn, d, cal):
    """Queue erase-and-check for the current rows `auto` would accept that have no check for their answer. Yes/no
    answers depend on the calibration's bands, so those rows are checked again after every recalibration."""
    rows = conn.execute(
        "SELECT c.row_key, j.answer, r.judgment_id IS NOT NULL FROM docket.current c "
        "JOIN docket.judgment j ON j.id = c.judgment_id LEFT JOIN docket.robust r ON r.judgment_id = j.id "
        "WHERE c.decision_id = %s AND c.version = %s", (d["id"], d["version"])).fetchall()
    redo = d["spec"]["kind"] == "noul"
    keys = [k for k, a, checked in rows if (redo or not checked) and calibration.auto(a, d["spec"], cal)]
    if keys:
        conn.execute("INSERT INTO docket.queue (decision_id, version, row_key, purpose) "
                     "SELECT %s, %s, k, 'check' FROM unnest(%s::text[]) AS t(k) "
                     "ON CONFLICT (decision_id, version, row_key, purpose) DO NOTHING", (d["id"], d["version"], keys))
        conn.execute("SELECT pg_notify('docket', %s)", (str(d["id"]),))
    return len(keys)


def _calibrate(conn, d, precision_target=0.9, alpha=0.05, include_manual=False, per_answer=False, recall_target=None,
               harden=False):
    sampling = ["random", "all", "manual"] if include_manual else ["random", "all"]
    rows = conn.execute(
        "SELECT l.label, j.answer FROM docket.label l "
        "JOIN docket.current c ON c.decision_id = l.decision_id AND c.version = %s AND c.row_key = l.row_key "
        "JOIN docket.judgment j ON j.id = c.judgment_id WHERE l.decision_id = %s AND l.sampling = ANY(%s) "
        "ORDER BY l.row_key", (d["version"], d["id"], sampling)).fetchall()
    spec = d["spec"]
    # choice labels naming another version's option stay in: they count as wrong for this one
    labels = [r[0] for r in rows] if spec["kind"] == "choice" else [r[0] == "yes" for r in rows]  # yes/no normalised
    cal = calibration.fit(spec, [r[1] for r in rows], labels, precision_target, alpha, per_answer,
                          recall_target) | {"sampling": sampling}
    if harden:
        cal["harden"] = True
    conn.execute("UPDATE docket.version SET calibration = %s WHERE decision_id = %s AND version = %s",
                 (Jsonb(cal), d["id"], d["version"]))
    if harden:
        cal["checks_queued"] = harden_enqueue(conn, d, cal)
    return cal


def status(dsn):
    with connect(dsn) as conn:
        cur = conn.execute("SELECT * FROM docket.status ORDER BY name")
        cols = [c.name for c in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


def load_spec(kind, question, options=None, options_file=None, backend="jev", model="jev-1.14.0", batch_size=1,
              untrusted=False):
    opts = []
    if options_file:
        with open(options_file) as f:
            opts = [o if isinstance(o, dict) else {"name": o} for o in json.load(f)]
    elif options:
        opts = [{"name": o.strip()} for o in options.split(",") if o.strip()]
    spec = {"kind": kind, "question": question, "backend": backend, "model": model, "batch_size": batch_size}
    if kind == "choice":
        spec["options"] = opts
    if untrusted:
        spec["untrusted"] = True
    return validate(spec)


def estimate(dsn, name, answer=None, where=None, level=0.95, include_manual=False):
    """Counts with error bars from docket.estimate() (prediction-powered inference)."""
    sampling = ["random", "all", "manual"] if include_manual else ["random"]
    with connect(dsn) as conn:
        cur = conn.execute("SELECT * FROM docket.estimate(%s, %s, %s, %s, %s)", (name, answer, where, level, sampling))
        cols = [c.name for c in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


def revise(dsn, name, question=None, options=None, options_file=None, model=None, backend=None, batch_size=None,
           backfill=False, untrusted=None):
    """Add a shadow version: the active spec with the given fields replaced. Nothing is judged unless `backfill`;
    `compare` judges a sample first. Returns (version, spec)."""
    with connect(dsn) as conn:
        spec = dict(_decision(conn, name)["spec"])
        if question:
            spec["question"] = question
        if options or options_file:
            spec["options"] = load_spec("choice", "x", options, options_file)["options"]
        for k, v in (("model", model), ("backend", backend), ("batch_size", batch_size), ("untrusted", untrusted)):
            if v is not None:
                spec[k] = v
        validate(spec)
        ver = conn.execute("SELECT docket.revise(%s, %s, %s)", (name, Jsonb(spec), backfill)).fetchone()[0]
    return ver, spec


def backfill(dsn, name, version=None):
    with connect(dsn) as conn:
        return conn.execute("SELECT docket.backfill(%s, %s)", (name, version)).fetchone()[0]


def compare_enqueue(dsn, name, version, n, seed="1"):
    with connect(dsn) as conn:
        return conn.execute("SELECT docket.compare_enqueue(%s, %s, %s, %s)", (name, version, n, str(seed))).fetchone()[0]


def drift_enqueue(dsn, name, n, seed="1"):
    """Queue re-asks of the active version on up to n labelled rows. Returns (rows queued, database time before)."""
    with connect(dsn) as conn:
        since = conn.execute("SELECT clock_timestamp()").fetchone()[0]
        return conn.execute("SELECT docket.drift_enqueue(%s, %s, %s)", (name, n, str(seed))).fetchone()[0], since


def unjudged(conn, did, active, version):
    """Rows the active version answers that `version` doesn't yet: promoting now would show them as pending."""
    return conn.execute(
        "SELECT count(*) FROM docket.current a WHERE a.decision_id = %s AND a.version = %s AND NOT EXISTS "
        "(SELECT 1 FROM docket.current s WHERE s.decision_id = a.decision_id AND s.version = %s AND s.row_key = a.row_key)",
        (did, active, version)).fetchone()[0]


def promote(dsn, name, version, force=False):
    """Make a shadow version active. Refused while it has not judged every row the active version answers, unless
    `force` (those rows show as pending until the worker catches up). Calibration belongs to a version: when the
    old one was calibrated, the new one is fitted on the same labels and precision target in the same transaction,
    so `auto` keeps working. Returns the stored comparison and the new calibration (or why there is none)."""
    with connect(dsn) as conn:
        d = _decision(conn, name)
        missing = unjudged(conn, d["id"], d["version"], version)
        if missing and not force:
            raise SystemExit(f"v{version} has not judged {missing:,} rows that v{d['version']} answers; promoting now would "
                             f"show them as pending. Run `pgdocket backfill {name} --version {version}` and the worker "
                             "first, or pass --force.")
        comparison, prev = conn.execute(
            "SELECT s.comparison, a.calibration FROM docket.version s, docket.version a WHERE s.decision_id = %s "
            "AND s.version = %s AND a.decision_id = s.decision_id AND a.version = %s", (d["id"], version, d["version"])
        ).fetchone()
        conn.execute("SELECT docket.promote(%s, %s)", (name, version))
        cal, why = None, "the previous version was not calibrated"
        if prev:
            try:
                cal = _calibrate(conn, _decision(conn, name), prev["precision_target"], prev.get("alpha", 0.05),
                                 "manual" in prev.get("sampling", []), prev.get("mode") == "per answer",
                                 prev.get("recall_target"), prev.get("harden", False))
            except ValueError as e:
                why = str(e)
    return {"previous": d["version"], "missing": missing, "comparison": comparison, "calibration": cal,
            "calibration_missing": None if cal else why}


def retire(dsn, name, version):
    with connect(dsn) as conn:
        conn.execute("SELECT docket.retire(%s, %s)", (name, version))


def versions(dsn, name):
    with connect(dsn) as conn:
        d = _decision(conn, name)
        cur = conn.execute(
            "SELECT v.version, v.status, v.created_at, v.promoted_at, v.spec, v.calibration IS NOT NULL AS calibrated, "
            "v.comparison->>'verdict' AS verdict, "
            "(SELECT count(*) FROM docket.current c WHERE c.decision_id = v.decision_id AND c.version = v.version) AS judged "
            "FROM docket.version v WHERE v.decision_id = %s ORDER BY v.version", (d["id"],))
        cols = [c.name for c in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]
