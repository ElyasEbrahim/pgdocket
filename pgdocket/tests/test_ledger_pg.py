"""Ledger integration tests against a real Postgres (set TEST_DATABASE_URL to a superuser connection).

The superuser is only used to create a throwaway database and two roles. Everything pgdocket does runs as an
ordinary role (`owner`), and the application writes to its table as a second role (`app`) that has no grant
at all on the docket schema. This is the setup of a managed Postgres.
"""
import csv
import json
import os
import uuid

import pytest

psycopg = pytest.importorskip("psycopg")
from psycopg.conninfo import make_conninfo  # noqa: E402

from pgdocket import calibration, ledger  # noqa: E402
from pgdocket.tests.fakes import FakeSystemOne, serve  # noqa: E402
from pgdocket.worker import Worker  # noqa: E402

BASE = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not BASE, reason="set TEST_DATABASE_URL to run ledger integration tests")


@pytest.fixture(scope="module")
def env():
    tag = uuid.uuid4().hex[:8]
    db, owner, app, pw = f"docket_it_{tag}", f"docket_owner_{tag}", f"docket_app_{tag}", "pw-" + tag
    with psycopg.connect(BASE, autocommit=True) as su:
        su.execute(f"CREATE ROLE {owner} LOGIN PASSWORD '{pw}' NOSUPERUSER NOCREATEDB NOCREATEROLE")
        su.execute(f"CREATE ROLE {app} LOGIN PASSWORD '{pw}' NOSUPERUSER")
        su.execute(f"CREATE DATABASE {db} OWNER {owner}")
    owner_dsn = make_conninfo(BASE, dbname=db, user=owner, password=pw)
    app_dsn = make_conninfo(BASE, dbname=db, user=app, password=pw)
    with psycopg.connect(owner_dsn, autocommit=True) as c:
        c.execute("CREATE SCHEMA shop")
        c.execute("CREATE TABLE shop.tickets (id int PRIMARY KEY, subject text, body text, priority int, team text)")
        c.execute(f"GRANT USAGE ON SCHEMA shop TO {app}")
        c.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON shop.tickets TO {app}")
    url, shutdown = serve()
    yield {"owner": owner_dsn, "app": app_dsn, "url": url}
    shutdown()
    with psycopg.connect(BASE, autocommit=True) as su:
        su.execute(f"DROP DATABASE IF EXISTS {db} WITH (FORCE)")
        su.execute(f"DROP ROLE IF EXISTS {owner}")
        su.execute(f"DROP ROLE IF EXISTS {app}")


OPTIONS = [{"name": "billing", "description": "invoices and refunds"}, {"name": "technical"}, {"name": "sales"}]


def _spec(url):
    return {"kind": "choice", "question": "Which team should handle this ticket?", "options": OPTIONS,
            "backend": url, "model": "fake", "batch_size": 1}


def _view(dsn):
    with psycopg.connect(dsn) as c:
        return {r[0]: r[1:] for r in c.execute("SELECT key, status, answer, confidence, version FROM docket.v_triage ORDER BY key")}


def test_install_is_idempotent_and_needs_no_superuser(env):
    with psycopg.connect(env["owner"]) as c:
        assert c.execute("SELECT rolsuper FROM pg_roles WHERE rolname = current_user").fetchone()[0] is False
    assert ledger.install(env["owner"]) == ["001", "002", "003", "004", "005", "006", "007", "008", "009"]
    assert ledger.install(env["owner"]) == []


def test_create_backfills_and_app_inserts_enqueue_without_grants(env):
    with psycopg.connect(env["owner"], autocommit=True) as c:
        c.execute("INSERT INTO shop.tickets VALUES (1, 'refund', 'billing problem', 1, 'billing')")
    did, queued = ledger.create(env["owner"], "triage", "shop.tickets", "id", ["subject", "body"], _spec(env["url"]))
    assert queued == 1                                        # the existing row was backfilled
    with psycopg.connect(env["app"], autocommit=True) as c:   # the app role can't even see docket.*
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute("SELECT * FROM docket.queue")
    with psycopg.connect(env["app"], autocommit=True) as c:
        c.execute("INSERT INTO shop.tickets VALUES (2, 'crash', 'technical error', 2, 'technical'), "
                  "(3, 'quote', 'sales question', 3, 'sales')")
    v = _view(env["owner"])
    assert {k: s[0] for k, s in v.items()} == {1: "pending", 2: "pending", 3: "pending"}


def test_worker_judges_and_view_reads(env):
    totals = Worker(env["owner"], poll=0.1, log=lambda *a: None).run(once=True)
    assert totals["judged"] == 3
    v = _view(env["owner"])
    assert {k: (s[0], s[1]) for k, s in v.items()} == {1: ("judged", "billing"), 2: ("judged", "technical"),
                                                       3: ("judged", "sales")}
    assert all(abs(s[2] - 0.9) < 1e-9 for s in v.values())    # uncalibrated: the raw top probability
    with psycopg.connect(env["owner"]) as c:
        assert c.execute("SELECT count(*) FROM docket.queue").fetchone()[0] == 0
        assert c.execute("SELECT count(*) FROM docket.judgment").fetchone()[0] == 3


def test_only_changes_to_judged_columns_requeue(env):
    with psycopg.connect(env["app"], autocommit=True) as c:
        c.execute("UPDATE shop.tickets SET priority = 9 WHERE id = 1")             # not a judged column
        c.execute("UPDATE shop.tickets SET body = body WHERE id = 2")              # same value
        c.execute("UPDATE shop.tickets SET body = 'sales lead after all' WHERE id = 3")
    with psycopg.connect(env["owner"]) as c:
        assert [r[0] for r in c.execute("SELECT row_key FROM docket.queue")] == ["3"]
    Worker(env["owner"], poll=0.1, log=lambda *a: None).run(once=True)
    with psycopg.connect(env["owner"]) as c:
        assert c.execute("SELECT count(*) FROM docket.judgment WHERE row_key = '3'").fetchone()[0] == 2  # ledger keeps both


def test_unchanged_input_is_not_judged_again(env):
    with psycopg.connect(env["owner"], autocommit=True) as c:
        c.execute("SELECT docket.backfill('triage')")                           # re-queue every row
    totals = Worker(env["owner"], poll=0.1, log=lambda *a: None).run(once=True)
    assert totals["judged"] == 0 and totals["skipped_unchanged"] == 3


def test_deleted_rows_leave_the_view_but_stay_in_the_ledger(env):
    with psycopg.connect(env["app"], autocommit=True) as c:
        c.execute("INSERT INTO shop.tickets VALUES (4, 'bill', 'billing again', 1, 'billing')")
        c.execute("DELETE FROM shop.tickets WHERE id = 4")
    totals = Worker(env["owner"], poll=0.1, log=lambda *a: None).run(once=True)
    assert totals["deleted_rows"] == 1
    assert 4 not in _view(env["owner"])


def test_backend_errors_back_off_then_dead_letter(env):
    FakeSystemOne.fail_rows_containing = "POISON"
    try:
        with psycopg.connect(env["app"], autocommit=True) as c:
            c.execute("INSERT INTO shop.tickets VALUES (5, 'x', 'POISON', 1, 'billing')")
        w = Worker(env["owner"], poll=0.1, log=lambda *a: None)
        for _ in range(5):                                                       # skip the back-off waits
            with psycopg.connect(env["owner"], autocommit=True) as c:
                c.execute("UPDATE docket.queue SET locked_until = NULL WHERE row_key = '5'")
            w.process_once()
        assert _view(env["owner"])[5][0] == "error"
        with psycopg.connect(env["owner"]) as c:
            attempts, err = c.execute("SELECT attempts, last_error FROM docket.queue WHERE row_key = '5'").fetchone()
        assert attempts == 5 and "400" in err
    finally:
        FakeSystemOne.fail_rows_containing = None


def test_labels_calibration_and_sql_python_parity(env):
    teams = ["billing", "technical", "sales"]
    # The fake model answers the team named in the body; the label disagrees for every third row, so the model's
    # 0.9 confidence is too high (it's right about 2/3 of the time) and calibration must pull it down.
    rows = [(100 + i, f"t{i}", teams[i % 3], 1, teams[i % 3] if i % 3 else teams[(i + 1) % 3]) for i in range(90)]
    with psycopg.connect(env["app"], autocommit=True) as c:
        with c.cursor() as cur:
            cur.executemany("INSERT INTO shop.tickets VALUES (%s, %s, %s, %s, %s)", rows)
    Worker(env["owner"], poll=0.1, log=lambda *a: None).run(once=True)
    assert ledger.import_labels(env["owner"], "triage", column="team") >= 90
    cal = ledger.calibrate(env["owner"], "triage", precision_target=0.5)
    assert cal["method"] == "temperature" and cal["n_labels"] >= 90
    with psycopg.connect(env["owner"]) as c:
        spec, stored = c.execute("SELECT spec, calibration FROM docket.version WHERE version = 1").fetchone()
        pairs = c.execute("SELECT j.answer, docket.calibrated(j.answer, %s, %s) FROM docket.judgment j",
                          (json.dumps(spec), json.dumps(stored))).fetchall()
        view_conf = c.execute("SELECT count(*) FROM docket.v_triage WHERE confidence < 0.9").fetchone()[0]
    for answer, sql_value in pairs:
        assert sql_value == pytest.approx(calibration.calibrated(answer, spec, stored), rel=1e-9)
    assert view_conf > 0                                    # the over-confident 0.9s were pulled down
    assert stored["threshold_on"] == "raw score"
    with psycopg.connect(env["owner"]) as c:                # the view's `auto` is the Python twin's, row by row
        rows = c.execute("SELECT v.auto, j.answer FROM docket.v_triage v JOIN docket.current cu ON cu.decision_id = "
                         "(SELECT id FROM docket.decision WHERE name = 'triage') AND cu.row_key = v.key::text "
                         "JOIN docket.judgment j ON j.id = cu.judgment_id").fetchall()
    assert rows and all(a == calibration.auto(ans, spec, stored) for a, ans in rows)


def test_status_view(env):
    s = {r["name"]: r for r in ledger.status(env["owner"])}["triage"]
    assert s["errors"] == 1 and s["labels"] >= 90 and s["calibrated"] and s["judged"] >= 93


def test_drop_decision_removes_triggers_view_and_ledger(env):
    with psycopg.connect(env["owner"], autocommit=True) as c:
        c.execute("SELECT docket.drop_decision('triage')")
        assert c.execute("SELECT to_regclass('docket.v_triage')").fetchone()[0] is None
        assert c.execute("SELECT count(*) FROM docket.judgment").fetchone()[0] == 0
    with psycopg.connect(env["app"], autocommit=True) as c:                 # inserts no longer enqueue
        c.execute("INSERT INTO shop.tickets VALUES (999, 'x', 'billing', 1, 'billing')")
    with psycopg.connect(env["owner"]) as c:
        assert c.execute("SELECT count(*) FROM docket.queue").fetchone()[0] == 0
        assert c.execute("SELECT count(*) FROM shop.tickets").fetchone()[0] > 90   # the source table is untouched


def _counts_setup(env):
    """400 orders; the fake model answers the team named in `body`, which disagrees with the human `team` for 1 in 4."""
    teams = ["billing", "technical", "sales"]
    with psycopg.connect(env["owner"], autocommit=True) as c:
        c.execute("CREATE TABLE shop.orders (id int PRIMARY KEY, body text, team text, urgent text)")
        rows = [(i, teams[i % 3] if i % 4 else teams[(i + 1) % 3], teams[i % 3], "yes" if i % 5 == 0 else "no")
                for i in range(1, 401)]
        with c.cursor() as cur:
            cur.executemany("INSERT INTO shop.orders VALUES (%s, %s, %s, %s)", rows)
    spec = dict(_spec(env["url"]))
    ledger.create(env["owner"], "counts", "shop.orders", "id", ["body"], spec)
    Worker(env["owner"], poll=0.1, log=lambda *a: None).run(once=True)


def test_estimate_matches_python_and_respects_sampling(env):
    from pgdocket.estimate import ppi_share
    _counts_setup(env)
    # whole-column labels are not a random sample: refused by default
    ledger.import_labels(env["owner"], "counts", column="team")
    rows = ledger.estimate(env["owner"], "counts")
    assert all(r["estimate"] is None and "no random-sample labels" in r["note"] for r in rows)
    assert all(r["estimate"] is not None for r in ledger.estimate(env["owner"], "counts", include_manual=True))
    # a random sample of 120
    with psycopg.connect(env["owner"], autocommit=True) as c:
        c.execute("DELETE FROM docket.label WHERE decision_id = (SELECT id FROM docket.decision WHERE name = 'counts')")
    assert ledger.import_labels(env["owner"], "counts", column="team", sample=120, seed="7") == 120
    rows = {r["answer"]: r for r in ledger.estimate(env["owner"], "counts")}
    with psycopg.connect(env["owner"]) as c:
        data = c.execute(
            "SELECT v.answer, t.team, l.label IS NOT NULL FROM docket.v_counts v JOIN shop.orders t ON t.id = v.key "
            "LEFT JOIN docket.label l ON l.decision_id = (SELECT id FROM docket.decision WHERE name = 'counts') "
            "AND l.row_key = v.key::text AND l.sampling = 'random'").fetchall()
    for answer in ["billing", "technical", "sales"]:
        f = [int(a == answer) for a, _, _ in data]
        y = [int(t == answer) for _, t, _ in data]
        lab = [x for _, _, x in data]
        py = ppi_share([fi for fi, l in zip(f, lab) if not l], [fi for fi, l in zip(f, lab) if l],
                       [yi for yi, l in zip(y, lab) if l])
        r = rows[answer]
        assert r["labels"] == 120 and r["judged"] == 400
        assert r["estimate"] == pytest.approx(py["estimate"] * 400, rel=1e-9)
        assert r["ci_low"] == pytest.approx(py["ci_low"] * 400, rel=1e-9)
        assert r["ci_high"] == pytest.approx(py["ci_high"] * 400, rel=1e-9)
        assert r["labels_only_low"] == pytest.approx(py["labels_low"] * 400, rel=1e-9)
        assert r["lambda"] == pytest.approx(py["lam"], rel=1e-9, abs=1e-12)
        assert r["reliable"] == py["reliable"]
        truth = sum(y)
        assert r["ci_low"] <= truth <= r["ci_high"]                      # this draw covers the truth


def test_estimate_scope_uses_only_labels_in_scope(env):
    rows = ledger.estimate(env["owner"], "counts", answer="billing", where="id <= 200")
    with psycopg.connect(env["owner"]) as c:
        n = c.execute("SELECT count(*) FROM docket.label l JOIN docket.decision d ON d.id = l.decision_id "
                      "WHERE d.name = 'counts' AND l.sampling = 'random' AND l.row_key::int <= 200").fetchone()[0]
    assert rows[0]["judged"] == 200 and rows[0]["labels"] == n


def test_yes_no_labels_are_normalised_and_estimated(env):
    spec = {"kind": "noul", "question": "the order is urgent", "backend": env["url"], "model": "fake", "batch_size": 1}
    ledger.create(env["owner"], "urgent", "shop.orders", "id", ["body"], spec)
    Worker(env["owner"], poll=0.1, log=lambda *a: None).run(once=True)
    with pytest.raises(SystemExit):
        ledger.import_labels(env["owner"], "urgent", column="urgent", sample=150)          # needs --positive
    ledger.import_labels(env["owner"], "urgent", column="urgent", sample=150, positive="yes")
    with psycopg.connect(env["owner"]) as c:
        assert {r[0] for r in c.execute("SELECT DISTINCT label FROM docket.label l JOIN docket.decision d "
                                        "ON d.id = l.decision_id WHERE d.name = 'urgent'")} == {"yes", "no"}
    r = ledger.estimate(env["owner"], "urgent")[0]
    assert r["answer"] == "yes" and r["labels"] == 150 and r["estimate"] is not None


def test_compare_a_shadow_version_then_promote(env, capsys):
    """Revise → compare on a sample (probes leave answers alone) → promotion gated on full coverage → promote."""
    from pgdocket import cli, compare
    teams = ["billing", "technical", "sales"]
    with psycopg.connect(env["owner"], autocommit=True) as c:
        c.execute("CREATE TABLE shop.routes (id int PRIMARY KEY, body text, team text)")
        with c.cursor() as cur:
            cur.executemany("INSERT INTO shop.routes VALUES (%s, %s, %s)",
                            [(i, teams[i % 3], teams[i % 3]) for i in range(1, 121)])
    ledger.create(env["owner"], "routes", "shop.routes", "id", ["body"], _spec(env["url"]))
    Worker(env["owner"], poll=0.1, log=lambda *a: None).run(once=True)
    ledger.import_labels(env["owner"], "routes", column="team", sample=45)
    before = _routes(env["owner"])

    ver, spec = ledger.revise(env["owner"], "routes", options="billing,engineering,sales")
    assert ver == 2 and spec["model"] == "fake" and [o["name"] for o in spec["options"]] == ["billing", "engineering", "sales"]
    assert ledger.compare_enqueue(env["owner"], "routes", 2, 60) == 60
    with psycopg.connect(env["owner"]) as c:
        assert dict(c.execute("SELECT purpose, count(*) FROM docket.queue WHERE decision_id = "
                              "(SELECT id FROM docket.decision WHERE name = 'routes') GROUP BY 1").fetchall()) == \
            {"live": 60, "probe": 60}
        assert c.execute("SELECT probes_queued, shadow_versions FROM docket.status WHERE name = 'routes'").fetchone() == (60, 1)
    totals = Worker(env["owner"], poll=0.1, log=lambda *a: None).run(once=True)
    assert (totals["judged"], totals["probed"]) == (60, 60)
    assert _routes(env["owner"]) == before                    # probes and the shadow version leave the view alone

    with psycopg.connect(env["owner"]) as c:
        data = compare.fetch(c, "routes", 2)
    r = compare.analyse(data["spec_old"], data["spec_new"], data["rows"], n_boot=300)
    assert r["n"] == 60 and r["labels"] == 45 and r["noise"] == 0  # labelled rows were sampled first
    assert r["transitions"][0]["old"] == "technical" and r["transitions"][0]["new"] == "billing"
    assert r["verdict"] == "keep current" and r["labels_not_options_of_new"] > 0

    # label only the rows where the versions disagree (the people's answer is the `team` column)
    path = os.path.join(os.environ.get("TMPDIR", "/tmp"), f"disagree-{uuid.uuid4().hex[:6]}.csv")
    cli.main(["compare", "--dsn", env["owner"], "routes", "2", "--export-disagreements", path])
    out = capsys.readouterr().out
    assert "Verdict: **keep current**" in out
    with open(path, newline="") as f:
        todo = list(csv.DictReader(f))
    assert len(todo) == r["disagreements"] - r["disagreements_labelled"] > 0
    assert {t["v1"] for t in todo} == {"technical"} and {t["v2"] for t in todo} == {"billing"}
    with psycopg.connect(env["owner"]) as c:
        drawn = c.execute("SELECT l.row_key FROM docket.label l JOIN docket.decision d ON d.id = l.decision_id "
                          "WHERE d.name = 'routes' AND l.sampling = 'random' ORDER BY 1 LIMIT 1").fetchone()[0]
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["key", "label"])
        w.writerows([(t["key"], t["body"]) for t in todo] + [(drawn, teams[int(drawn) % 3])])
    assert ledger.import_labels(env["owner"], "routes", csv_path=path) == len(todo) + 1
    with psycopg.connect(env["owner"]) as c:
        sampling = dict(c.execute("SELECT l.sampling, count(*) FROM docket.label l JOIN docket.decision d "
                                  "ON d.id = l.decision_id WHERE d.name = 'routes' GROUP BY 1").fetchall())
        assert sampling == {"random": 45, "manual": len(todo)}   # re-labelling a drawn row keeps it in the sample
        data = compare.fetch(c, "routes", 2)
        tech = c.execute("SELECT count(*) FROM docket.label l JOIN docket.decision d ON d.id = l.decision_id "
                         "WHERE d.name = 'routes' AND l.sampling = 'random' AND l.label = 'technical'").fetchone()[0]
    # hand-picked labels (the disagreements) stay out of calibration unless asked for
    assert ledger.calibrate(env["owner"], "routes", include_manual=True)["n_labels"] == 45 + len(todo)
    cal1 = ledger.calibrate(env["owner"], "routes")
    assert cal1["n_labels"] == 45 and cal1["sampling"] == ["random", "all"]
    r2 = compare.analyse(data["spec_old"], data["spec_new"], data["rows"], n_boot=300)
    assert r2["gain_from"] == "disagreements" and r2["disagreements_right"]["current"] == r2["disagreements"]
    assert r2["verdict"] == "keep current"
    with pytest.raises(SystemExit, match="has not judged 60 rows"):
        ledger.promote(env["owner"], "routes", 2)
    assert ledger.backfill(env["owner"], "routes", 2) == 120
    totals = Worker(env["owner"], poll=0.1, log=lambda *a: None).run(once=True)
    assert (totals["judged"], totals["skipped_unchanged"]) == (60, 60)   # the sample is not asked twice
    p = ledger.promote(env["owner"], "routes", 2)
    assert p["previous"] == 1 and p["missing"] == 0 and p["comparison"]["verdict"] == "keep current"
    # recalibrated in the same step, on the same labels: v2 can't answer `technical`, so those labels count as wrong
    cal2 = p["calibration"]
    assert cal2, p["calibration_missing"]
    assert cal2["n_labels"] == 45 and cal2["labels_not_options"] == tech > 0
    assert cal2["precision_target"] == cal1["precision_target"] and cal2["threshold"] is None
    with psycopg.connect(env["owner"]) as c:                  # calibrated, nothing reaches 90 %: auto is false
        assert c.execute("SELECT DISTINCT auto FROM docket.v_routes").fetchall() == [(False,)]
    after = _routes(env["owner"])
    assert {v[1] for v in after.values()} == {2} and "technical" not in {v[0] for v in after.values()}
    vs = {v["version"]: v for v in ledger.versions(env["owner"], "routes")}
    assert vs[1]["status"] == "retired" and vs[2]["status"] == "active" and vs[2]["promoted_at"] is not None
    with psycopg.connect(env["owner"]) as c:
        assert c.execute("SELECT count(*) FROM docket.judgment j JOIN docket.decision d ON d.id = j.decision_id "
                         "WHERE d.name = 'routes' AND j.purpose = 'probe'").fetchone()[0] == 60


def test_a_shadow_version_judges_new_rows_until_retired(env):
    ver, _ = ledger.revise(env["owner"], "routes", model="fake-2")
    with psycopg.connect(env["owner"], autocommit=True) as c:
        c.execute("INSERT INTO shop.routes VALUES (500, 'sales', 'sales')")
    with psycopg.connect(env["owner"]) as c:
        queued = c.execute("SELECT version FROM docket.queue q JOIN docket.decision d ON d.id = q.decision_id "
                           "WHERE d.name = 'routes' AND row_key = '500' ORDER BY 1").fetchall()
    assert queued == [(2,), (ver,)]                       # the active version and the shadow both judge it
    ledger.retire(env["owner"], "routes", ver)
    with psycopg.connect(env["owner"], autocommit=True) as c:
        c.execute("INSERT INTO shop.routes VALUES (501, 'sales', 'sales')")
    with psycopg.connect(env["owner"]) as c:
        assert c.execute("SELECT count(*) FROM docket.queue q JOIN docket.decision d ON d.id = q.decision_id "
                         "WHERE d.name = 'routes' AND q.version = %s", (ver,)).fetchone()[0] == 0
    assert {v["version"]: v["status"] for v in ledger.versions(env["owner"], "routes")} == \
        {1: "retired", 2: "active", ver: "retired"}
    with pytest.raises(psycopg.errors.RaiseException, match="no shadow version"):
        ledger.retire(env["owner"], "routes", 2)


def _routes(dsn):
    with psycopg.connect(dsn) as c:
        return {r[0]: r[1:] for r in c.execute("SELECT key, answer, version FROM docket.v_routes ORDER BY key")}


def test_compare_waits_for_a_running_worker(env, capsys):
    """`compare --sample N --wait S`: queue the comparison, let the worker that is already running judge it, report."""
    import threading
    import time
    from pgdocket import cli
    ver, _ = ledger.revise(env["owner"], "routes", question="Which team should own this ticket?")
    worker = threading.Thread(target=lambda: (time.sleep(2), Worker(env["owner"], poll=0.1, log=lambda *a: None).run(once=True)))
    worker.start()
    cli.main(["compare", "--dsn", env["owner"], "routes", str(ver), "--sample", "20", "--wait", "60"])
    worker.join()
    out = capsys.readouterr().out
    assert "20 rows queued" in out and "waited: comparison judged" in out and "· 20 rows compared" in out
    assert "not done yet" not in out
    ledger.retire(env["owner"], "routes", ver)


def test_each_comparison_keeps_its_own_noise_baseline(env):
    """A later comparison re-asks the active version again; an earlier report must not pick up those re-asks."""
    from pgdocket import compare
    quiet = lambda: Worker(env["owner"], poll=0.1, log=lambda *a: None).run(once=True)

    def noise(version):
        with psycopg.connect(env["owner"]) as c:
            d = compare.fetch(c, "routes", version)
        return compare.analyse(d["spec_old"], d["spec_new"], d["rows"], n_boot=100)["noise"]

    a, _ = ledger.revise(env["owner"], "routes", question="Which team should take this ticket?")
    ledger.compare_enqueue(env["owner"], "routes", a, 30)
    quiet()
    assert noise(a) == 0
    FakeSystemOne.answer_override = {"s": "b"}              # the model now says billing for sales tickets
    try:
        b, _ = ledger.revise(env["owner"], "routes", question="Which team should own this request?")
        ledger.compare_enqueue(env["owner"], "routes", b, 30)
        quiet()
    finally:
        FakeSystemOne.answer_override = {}
    assert noise(b) > 0                                     # b's re-asks saw the changed model
    assert noise(a) == 0                                    # a keeps the re-asks made at its own time
    ledger.retire(env["owner"], "routes", a)
    ledger.retire(env["owner"], "routes", b)


def test_per_answer_guarantee_in_the_view_and_across_promote(env):
    """`calibrate --per-answer`: the view accepts a row by its own answer's threshold, exactly as the Python twin."""
    rows = ([(i, "billing", "billing") for i in range(1, 121)] + [(i, "technical", "technical") for i in range(121, 241)]
            + [(i, "sales", "sales" if i % 2 else "billing") for i in range(241, 301)])     # sales: right half the time
    with psycopg.connect(env["owner"], autocommit=True) as c:
        c.execute("CREATE TABLE shop.mail (id int PRIMARY KEY, body text, team text)")
        with c.cursor() as cur:
            cur.executemany("INSERT INTO shop.mail VALUES (%s, %s, %s)", rows)
    ledger.create(env["owner"], "mail", "shop.mail", "id", ["body"], _spec(env["url"]))
    Worker(env["owner"], poll=0.1, log=lambda *a: None).run(once=True)
    ledger.import_labels(env["owner"], "mail", column="team")

    overall = ledger.calibrate(env["owner"], "mail", precision_target=0.8)
    assert overall["threshold"] is not None and overall["coverage"] == 1.0              # 90 % right in total...
    assert [r["answer"] for r in overall["per_answer"] if r["below_target"]] == ["sales"]   # ...but sales is not

    cal = ledger.calibrate(env["owner"], "mail", precision_target=0.8, per_answer=True)
    assert cal["mode"] == "per answer" and cal["thresholds"]["sales"] is None
    assert cal["thresholds"]["billing"] is not None and cal["thresholds"]["technical"] is not None
    with psycopg.connect(env["owner"]) as c:
        spec = c.execute("SELECT v.spec FROM docket.version v JOIN docket.decision d ON d.id = v.decision_id "
                         "WHERE d.name = 'mail' AND v.version = 1").fetchone()[0]
        view = c.execute("SELECT v.answer, v.auto, j.answer FROM docket.v_mail v JOIN docket.current cu ON cu.decision_id = "
                         "(SELECT id FROM docket.decision WHERE name = 'mail') AND cu.row_key = v.key::text "
                         "JOIN docket.judgment j ON j.id = cu.judgment_id").fetchall()
    assert len(view) == 300 and all(auto == calibration.auto(ans, spec, cal) for _, auto, ans in view)
    assert {a for a, auto, _ in view if auto} == {"billing", "technical"}

    ver, _ = ledger.revise(env["owner"], "mail", question="Which team should answer this email?", backfill=True)
    Worker(env["owner"], poll=0.1, log=lambda *a: None).run(once=True)
    p = ledger.promote(env["owner"], "mail", ver)
    assert p["calibration"]["mode"] == "per answer" and p["calibration"]["thresholds"]["sales"] is None


def test_plan_reads_the_view_and_exports_the_review_queue(env, capsys):
    from pgdocket import cli, plan
    ledger.import_labels(env["owner"], "mail", column="team", sample=150)      # 150 of the 300 drawn at random
    with psycopg.connect(env["owner"]) as c:
        rows = plan.fetch(c, "mail")["rows"]
    assert len(rows) == 300 and sum(r["label"] is not None for r in rows) == 150
    p = plan.from_rows(rows, 0.05)
    lab = [r for r in rows if r["label"] is not None]
    assert p["ai_error"] == sum(r["answer"] != r["label"] for r in lab) / 150
    path = os.path.join(os.environ.get("TMPDIR", "/tmp"), f"queue-{uuid.uuid4().hex[:6]}.csv")
    cli.main(["plan", "--dsn", env["owner"], "mail", "--max-error", "0.05", "--export", path])
    out = capsys.readouterr().out
    assert "150 random labels" in out and "must review every row" in out     # every answer is 0.9: no safe cut
    with open(path, newline="") as f:
        queue = list(csv.DictReader(f))
    assert len(queue) == 150 and set(queue[0]) == {"key", "body", "ai_answer", "ai_confidence", "label"}
    cli.main(["plan", "--dsn", env["owner"], "mail", "--max-error", "0.2"])
    assert "no review needed" in capsys.readouterr().out                     # 10 % wrong: the AI may keep all


def test_calibrate_before_the_worker_ran_says_what_to_do(env):
    """A newcomer who calibrates straight after `label` gets a plain message, not a traceback."""
    from pgdocket import cli
    with psycopg.connect(env["owner"], autocommit=True) as c:
        c.execute("CREATE TABLE shop.fresh (id int PRIMARY KEY, body text, team text)")
        c.execute("INSERT INTO shop.fresh SELECT i, 'billing', 'billing' FROM generate_series(1, 40) i")
    ledger.create(env["owner"], "fresh", "shop.fresh", "id", ["body"], _spec(env["url"]))
    ledger.import_labels(env["owner"], "fresh", column="team")
    with pytest.raises(SystemExit, match="can't calibrate yet: only 0 labeled rows .* 40 rows are still queued"):
        cli.main(["calibrate", "--dsn", env["owner"], "fresh"])
    Worker(env["owner"], poll=0.1, log=lambda *a: None).run(once=True)
    cli.main(["calibrate", "--dsn", env["owner"], "fresh"])                      # now it works


def test_recall_target_gives_the_view_three_bands(env):
    """Yes/no with a recall target: auto-yes, auto-no and a review band, in SQL exactly as in Python (#21)."""
    rows = ([(i, "yes", "yes" if i <= 57 else "no") for i in range(1, 61)]           # model says yes: 57 of 60 right
            + [(i, "no", "yes" if i == 61 else "no") for i in range(61, 201)])      # model says no: misses 1 yes
    with psycopg.connect(env["owner"], autocommit=True) as c:
        c.execute("CREATE TABLE shop.flags (id int PRIMARY KEY, want text, truth text)")
        with c.cursor() as cur:
            cur.executemany("INSERT INTO shop.flags VALUES (%s, %s, %s)", rows)
    spec = {"kind": "noul", "question": "Is it urgent?", "backend": env["url"], "model": "fake", "batch_size": 1}
    ledger.create(env["owner"], "flags", "shop.flags", "id", ["want"], spec)
    Worker(env["owner"], poll=0.1, log=lambda *a: None).run(once=True)
    ledger.import_labels(env["owner"], "flags", column="truth", positive="yes")

    def view(cal):
        with psycopg.connect(env["owner"]) as c:
            stored = c.execute("SELECT v.spec FROM docket.version v JOIN docket.decision d ON d.id = v.decision_id "
                               "WHERE d.name = 'flags' AND v.status = 'active'").fetchone()[0]
            got = c.execute("SELECT v.answer, v.auto, j.answer FROM docket.v_flags v JOIN docket.current cu ON cu.decision_id = "
                            "(SELECT id FROM docket.decision WHERE name = 'flags') AND cu.row_key = v.key::text "
                            "JOIN docket.judgment j ON j.id = cu.judgment_id").fetchall()
        assert len(got) == 200
        for answer, auto, raw in got:
            assert auto == calibration.auto(raw, stored, cal) and answer == calibration.noul_answer(raw, stored, cal)
        return got

    cal = ledger.calibrate(env["owner"], "flags", precision_target=0.99, recall_target=0.9)   # 99 %: no auto-yes
    assert cal["threshold"] is None and cal["recall_threshold"] == 0.8 and cal["positives"] == 58
    got = view(cal)
    assert {(a, auto) for a, auto, _ in got} == {("no", True), ("yes", False)}               # auto-no + review band
    cal = ledger.calibrate(env["owner"], "flags", precision_target=0.8, recall_target=0.9)
    got = view(cal)
    assert {(a, auto) for a, auto, _ in got} == {("no", True), ("yes", True)}                # both sides automatic
    ver, _ = ledger.revise(env["owner"], "flags", question="Is it urgent? Answer for the on-call team.", backfill=True)
    Worker(env["owner"], poll=0.1, log=lambda *a: None).run(once=True)
    assert ledger.promote(env["owner"], "flags", ver)["calibration"]["recall_target"] == 0.9


def _drift(env, *args):
    """Run `pgdocket drift`; returns its exit status."""
    from pgdocket import cli
    try:
        cli.main(["drift", "--dsn", env["owner"], "stream", *args])
        return 0
    except SystemExit as e:
        return e.code


def test_drift_sees_recent_rows_that_look_different(env, capsys):
    """Free data check (#22): rows dated by a source column; the last week is mostly sales, earlier weeks were not."""
    teams = ["billing", "technical", "sales"]
    rows = ([(i, teams[i % 3], teams[i % 3], 60, i) for i in range(1, 241)]                 # 60 to 50 days ago
            + [(i, "sales" if i % 5 else "billing", "sales" if i % 5 else "billing", 3, i - 240) for i in range(241, 301)])
    with psycopg.connect(env["owner"], autocommit=True) as c:
        c.execute("CREATE TABLE shop.stream (id int PRIMARY KEY, body text, team text, created_at timestamptz)")
        with c.cursor() as cur:
            cur.executemany("INSERT INTO shop.stream VALUES (%s, %s, %s, now() - make_interval(days => %s) "
                            "+ make_interval(hours => %s))", rows)
    ledger.create(env["owner"], "stream", "shop.stream", "id", ["body"], _spec(env["url"]))
    Worker(env["owner"], poll=0.1, log=lambda *a: None).run(once=True)
    ledger.import_labels(env["owner"], "stream", column="team", sample=150)

    code = _drift(env, "--by", "created_at", "--days", "7")
    out = capsys.readouterr().out
    assert code == 1 and "answers: sales 33.3 % → 80.0 %" in out and "⚠ shifted" in out
    code = _drift(env)                                        # by judged_at: every row was judged just now
    assert code == 0 and "needs at least 30 on each side; to date rows by a column" in capsys.readouterr().out


def test_drift_re_asks_labelled_rows_and_catches_a_changed_model(env, capsys):
    """Model check (#22): the same anchors asked again; the model starts answering sales tickets as technical."""
    code = _drift(env, "--anchors", "60", "--run")
    out = capsys.readouterr().out
    assert code == 0 and "60 labelled rows queued" in out and "no change in the model detected" in out
    FakeSystemOne.answer_override = {"s": "t"}
    try:
        code = _drift(env, "--anchors", "60", "--run")
    finally:
        FakeSystemOne.answer_override = {}
    out = capsys.readouterr().out
    assert code == 1 and "⚠ dropped" in out and "compare a new version" in out
    with psycopg.connect(env["owner"]) as c:
        runs = c.execute("SELECT alarm, (result->>'run')::int, (result->'accuracy'->>'worse')::int FROM docket.drift_check "
                         "WHERE kind = 'model' AND decision_id = (SELECT id FROM docket.decision WHERE name = 'stream') "
                         "ORDER BY id").fetchall()
        current = c.execute("SELECT count(*) FROM docket.v_stream WHERE answer = 'technical'").fetchone()[0]
    assert [(a, k) for a, k, _ in runs] == [(False, 1), (True, 2)] and runs[1][2] > 10
    assert current == 80                                      # re-asks are probes: the view's answers didn't change
    ledger.calibrate(env["owner"], "stream")                  # recalibrating starts a new series of checks
    code = _drift(env, "--model")                             # the anchors asked last time, reported again
    assert code == 1 and "check 1, false-alarm budget 0.0304" in capsys.readouterr().out



def test_audit_draws_blind_batches_and_catches_accepted_answers_going_wrong(env, capsys):
    """Audit (#23): random draws from rows marked auto since the last batch, labelled blind, kept apart from the
    calibration labels, and an alarm once the accepted answers are demonstrably below the target."""
    from pgdocket import cli
    teams = "ARRAY['billing', 'technical', 'sales']"
    with psycopg.connect(env["owner"], autocommit=True) as c:
        c.execute("CREATE TABLE shop.inbox (id int PRIMARY KEY, body text, team text)")
        c.execute(f"INSERT INTO shop.inbox SELECT i, ({teams})[i % 3 + 1], ({teams})[i % 3 + 1] FROM generate_series(1, 300) i")
    ledger.create(env["owner"], "inbox", "shop.inbox", "id", ["body"], _spec(env["url"]))
    run = lambda: Worker(env["owner"], poll=0.1, log=lambda *a: None).run(once=True)
    run()
    ledger.import_labels(env["owner"], "inbox", column="team", sample=100)
    assert ledger.calibrate(env["owner"], "inbox")["threshold"] is not None              # every answer is accepted

    def audit(*args):
        try:
            cli.main(["audit", "--dsn", env["owner"], "inbox", *args])
            return 0
        except SystemExit as e:
            return e.code

    path = os.path.join(os.environ.get("TMPDIR", "/tmp"), f"audit-{uuid.uuid4().hex[:6]}.csv")
    assert audit("--draw", "20", "--out", path) == 0
    assert "batch 1: 20 rows drawn at random from 200 accepted rows" in capsys.readouterr().out
    with open(path, newline="") as f:
        drawn = list(csv.DictReader(f))
    assert len(drawn) == 20 and list(drawn[0]) == ["key", "body", "label"]              # blind: no AI answer
    with open(path, "w", newline="") as f:                                              # people agree with the model
        w = csv.writer(f)
        w.writerow(["key", "body", "label"])
        w.writerows([r["key"], r["body"], r["body"]] for r in drawn)
    ledger.import_labels(env["owner"], "inbox", csv_path=path)
    assert audit() == 0 and "precision of the audited rows: 100.0 % (20 of 20)" in capsys.readouterr().out

    with psycopg.connect(env["owner"], autocommit=True) as c:                           # new mail: mostly sales
        c.execute(f"INSERT INTO shop.inbox SELECT i, b, b FROM (SELECT i, CASE WHEN i % 3 = 0 THEN 'billing' ELSE 'sales' END b "
                  "FROM generate_series(301, 420) i) s")
    FakeSystemOne.answer_override = {"s": "b"}                                          # ...and sales now goes to billing
    try:
        run()
    finally:
        FakeSystemOne.answer_override = {}
    code = audit("--draw", "40", "--from-column", "team")
    out = capsys.readouterr().out
    assert "batch 2: 40 rows drawn at random from 120 accepted rows" in out             # only rows judged since batch 1
    assert code == 1 and "⚠ the auto-accepted answers are below the 90.0 % target" in out
    with psycopg.connect(env["owner"]) as c:
        assert c.execute("SELECT count(*) FROM docket.label l JOIN docket.decision d ON d.id = l.decision_id "
                         "WHERE d.name = 'inbox' AND l.sampling = 'audit'").fetchone()[0] == 60
    assert ledger.calibrate(env["owner"], "inbox")["n_labels"] == 100                  # audit labels stay apart...
    assert audit() == 0 and "0 rows drawn, 0 labelled" in capsys.readouterr().out       # ...and a new calibration starts afresh


def test_harden_keeps_a_steered_row_out_of_auto(env, capsys):
    """#24: with a model that obeys text in the row, an override appended by the row's author sends it to the
    author's answer with full confidence. A hardened calibration keeps every row out of `auto` until erase-and-check
    has run, then flags the steered rows and passes the rest; new rows are checked as they are judged."""
    from pgdocket import cli
    from pgdocket.backend import UNTRUSTED
    from pgdocket.steer import attack
    teams = ["billing", "technical", "sales"]
    para = lambda t, c: t + " " + c * 150                       # each paragraph names the team: erasing one keeps it
    rows = [(i, f"ticket {i}", para(teams[i % 3], "x") + "\n\n" + para(teams[i % 3], "y"), teams[i % 3]) for i in range(1, 55)]
    rows += [(i, f"ticket {i}", para("billing", "x") + "\n\n" + para("billing", "y") + "\n\n"
              + attack("override", "choice", "sales"), "billing") for i in range(55, 61)]
    with psycopg.connect(env["owner"], autocommit=True) as c:
        c.execute("CREATE TABLE shop.desk (id int PRIMARY KEY, title text, body text, team text)")
        with c.cursor() as cur:
            cur.executemany("INSERT INTO shop.desk VALUES (%s, %s, %s, %s)", rows)
    spec = _spec(env["url"]) | {"untrusted": True}
    FakeSystemOne.obey_injection = True
    try:
        ledger.create(env["owner"], "desk", "shop.desk", "id", ["title", "body"], spec)
        Worker(env["owner"], poll=0.1, log=lambda *a: None).run(once=True)
        assert FakeSystemOne.requests[-1]["questions"]["r0"]["instructions"].endswith(UNTRUSTED)
        ledger.import_labels(env["owner"], "desk", column="team")

        def view():
            with psycopg.connect(env["owner"]) as c:
                return {k: (a, auto, chk) for k, a, auto, chk in
                        c.execute("SELECT key, answer, auto, erase_check FROM docket.v_desk")}
        ledger.calibrate(env["owner"], "desk", precision_target=0.8)
        assert {k: v[:2] for k, v in view().items() if k > 54} == {k: ("sales", True) for k in range(55, 61)}  # steered, auto
        cli.main(["calibrate", "--dsn", env["owner"], "desk", "--precision-target", "0.8", "--harden"])
        assert "hardened: 60 accepted rows queued for erase-and-check" in capsys.readouterr().out
        assert not any(auto for _, auto, _ in view().values())                     # nothing is auto until checked
        Worker(env["owner"], poll=0.1, log=lambda *a: None).run(once=True)
        v = view()
        assert all(v[k][1:] == (False, "flagged") for k in range(55, 61))          # the steered rows go to review
        assert all(v[k][1:] == (True, "passed") for k in range(1, 55))
        with psycopg.connect(env["owner"], autocommit=True) as c:                  # a new row is checked when judged
            c.execute("INSERT INTO shop.desk VALUES (61, 'ticket 61', %s, 'sales')", (para("sales", "x") + "\n\n" + para("sales", "y"),))
        Worker(env["owner"], poll=0.1, log=lambda *a: None).run(once=True)
        assert view()[61] == ("sales", True, "passed")
        ver, _ = ledger.revise(env["owner"], "desk", question="Which team should take this ticket?", backfill=True)
        Worker(env["owner"], poll=0.1, log=lambda *a: None).run(once=True)
        p = ledger.promote(env["owner"], "desk", ver)
        assert p["calibration"]["harden"] and p["calibration"]["checks_queued"] > 0   # hardening carries over
    finally:
        FakeSystemOne.obey_injection = False
