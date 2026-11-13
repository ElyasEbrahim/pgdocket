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
    assert ledger.install(env["owner"]) == ["001", "002", "003", "004", "005", "006", "007", "008", "009", "010", "011",
                                            "012", "013", "014", "015"]
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

    # four days on (#33): 60 more rows, still mostly sales. The window is tested against the rows from before the
    # series began, not against last week's shifted ones, and the evidence since the series' cut has piled up
    with psycopg.connect(env["owner"], autocommit=True) as c:
        with c.cursor() as cur:
            cur.executemany("INSERT INTO shop.stream VALUES (%s, %s, %s, now() + make_interval(days => 4) "
                            "+ make_interval(hours => %s))",
                            [(i, "sales" if i % 5 else "billing", "sales" if i % 5 else "billing", i - 300)
                             for i in range(301, 361)])
    Worker(env["owner"], poll=0.1, log=lambda *a: None).run(once=True)
    code = _drift(env, "--by", "created_at", "--days", "7")
    out = capsys.readouterr().out
    assert code == 1 and "against 240 from before the series began" in out and "check 2" in out   # a note isn't a check
    with psycopg.connect(env["owner"]) as c:
        r = c.execute("SELECT result FROM docket.drift_check WHERE kind = 'data' AND decision_id = "
                      "(SELECT id FROM docket.decision WHERE name = 'stream') ORDER BY id DESC LIMIT 1").fetchone()[0]
    ev = r["mix"]["evidence"]
    assert r["rows_since_cut"] == 120 and ev["alarm"] and ev["item"] in ("sales", "technical")
    assert "evidence since" in out and ev["log_e"] > ev["threshold"]


def test_drift_re_asks_labelled_rows_and_catches_a_changed_model(env, capsys):
    """Model check (#22, #35): each check re-asks the next labelled rows the series hasn't asked yet, and every row's
    first re-ask adds to the evidence; then the model starts answering sales tickets as technical."""
    code = _drift(env, "--anchors", "60", "--run")
    out = capsys.readouterr().out
    assert code == 0 and "60 labelled rows queued" in out and "no change in the model detected" in out
    assert "check 1" in out and "(needs p < 0.011)" in out and "evidence from 60 first re-asks" in out
    FakeSystemOne.answer_override = {"s": "t"}
    try:
        code = _drift(env, "--anchors", "60", "--run")
    finally:
        FakeSystemOne.answer_override = {}
    out = capsys.readouterr().out
    assert code == 1 and "⚠ dropped" in out and "compare a new version" in out
    with psycopg.connect(env["owner"]) as c:
        runs = c.execute("SELECT alarm, (result->>'run')::int, (result->'accuracy'->>'worse')::int, result "
                         "FROM docket.drift_check WHERE kind = 'model' AND decision_id = "
                         "(SELECT id FROM docket.decision WHERE name = 'stream') ORDER BY id").fetchall()
        asked = c.execute("SELECT count(DISTINCT row_key), count(*) FROM docket.judgment WHERE purpose = 'probe' AND "
                          "decision_id = (SELECT id FROM docket.decision WHERE name = 'stream')").fetchone()
        current = c.execute("SELECT count(*) FROM docket.v_stream WHERE answer = 'technical'").fetchone()[0]
    assert [(a, k) for a, k, _, _ in runs] == [(False, 1), (True, 2)] and runs[1][2] > 10
    assert tuple(asked) == (120, 120)                         # the second check re-asked 60 other rows (#35)
    second = runs[1][3]
    assert second["evidence_rows"] == 120 and second["accuracy"]["evidence"]["alarm"]
    assert current == 80                                      # re-asks are probes: the view's answers didn't change
    ledger.calibrate(env["owner"], "stream")                  # recalibrating starts a new series of checks
    code = _drift(env, "--model")                             # the anchors asked last time, reported again
    out = capsys.readouterr().out
    assert code == 1 and "check 1" in out and "(needs p < 0.011)" in out and "evidence from 0 first re-asks" in out


def test_drift_probes_re_ask_rows_without_labels(env, capsys):
    """#36: --probes M also re-asks M rows without labels, those the series hasn't asked yet. They add to the scores test
    and its evidence, never to accuracy; and once every labelled row has been asked, the check says so."""
    ledger.calibrate(env["owner"], "stream")                  # a new series
    code = _drift(env, "--anchors", "60", "--probes", "100", "--run")
    out = capsys.readouterr().out
    assert code == 0 and "100 rows without labels queued" in out and "60 labelled rows and 100 without labels" in out
    with psycopg.connect(env["owner"]) as c:
        r = c.execute("SELECT result FROM docket.drift_check WHERE kind = 'model' AND decision_id = "
                      "(SELECT id FROM docket.decision WHERE name = 'stream') ORDER BY id DESC LIMIT 1").fetchone()[0]
        probed = c.execute("""
            SELECT count(*) FILTER (WHERE l.row_key IS NULL), count(*) FROM docket.judgment j
            LEFT JOIN docket.label l ON l.decision_id = j.decision_id AND l.row_key = j.row_key
                                    AND l.sampling IN ('random', 'all')
            WHERE j.purpose = 'probe' AND j.decision_id = (SELECT id FROM docket.decision WHERE name = 'stream')
              AND j.judged_at > (SELECT (v.calibration->>'fitted_at')::timestamptz FROM docket.version v
                                 JOIN docket.decision d ON d.id = v.decision_id AND v.version = d.active_version
                                 WHERE d.name = 'stream')""").fetchone()
    assert tuple(probed) == (100, 160)                         # 100 rows without labels, 60 anchors
    assert r["probes"] == 100 and r["evidence_rows"] == 60 and r["evidence_rows_scores"] == 160 and r["labelled_left"] == 90
    assert r["scores"]["up"] + r["scores"]["down"] <= 160
    _drift(env, "--anchors", "60", "--run")
    assert "every labelled row has been asked" not in capsys.readouterr().out
    _drift(env, "--anchors", "60", "--run")                   # 150 labelled rows: the third check asks the last 30
    out = capsys.readouterr().out
    assert "every labelled row has been asked in this series" in out and "--probes M keeps the scores evidence" in out
    with pytest.raises(SystemExit, match="--probes goes with --anchors"):
        from pgdocket import cli
        cli.main(["drift", "--dsn", env["owner"], "stream", "--probes", "10"])


def test_drift_twice_asks_rows_without_labels_twice_and_sees_answers_change_in_no_one_direction(env, capsys):
    """#37, #38: --twice asks each --probes row twice, in requests of its own, and links the second ask to the first.
    Without a change no stored answer is the odd one out; once the model swaps two answers, the stored ones are, while
    the scores don't move. Each row counts once in a series."""
    from pgdocket import cli
    with pytest.raises(SystemExit, match="--twice goes with --probes"):
        cli.main(["drift", "--dsn", env["owner"], "stream", "--anchors", "10", "--twice"])
    with psycopg.connect(env["owner"], autocommit=True) as c:       # several rows per request: refused
        c.execute("UPDATE docket.version SET spec = jsonb_set(spec, '{batch_size}', '5') WHERE decision_id = "
                  "(SELECT id FROM docket.decision WHERE name = 'stream')")
        try:
            with pytest.raises(SystemExit, match="--twice needs one row per request, and stream sends 5"):
                cli.main(["drift", "--dsn", env["owner"], "stream", "--anchors", "10", "--probes", "10", "--twice"])
        finally:
            c.execute("UPDATE docket.version SET spec = jsonb_set(spec, '{batch_size}', '1') WHERE decision_id = "
                      "(SELECT id FROM docket.decision WHERE name = 'stream')")
        queued = c.execute("SELECT count(*) FROM docket.queue WHERE purpose = 'probe'").fetchone()[0]
    assert queued == 0                                        # refused before anything was queued

    ledger.calibrate(env["owner"], "stream")                  # a new series
    code = _drift(env, "--anchors", "30", "--probes", "60", "--twice", "--run")
    out = capsys.readouterr().out
    assert code == 0 and "60 rows without labels queued to be asked twice" in out
    assert "answers: of 60 rows asked twice since" in out and "0 had an odd one out, the stored answer on 0" in out
    assert "their scores: 0 had an odd one out, the stored score on 0 (a third expected) · evidence: 1 (needs 533)" in out
    assert "(needs 267)" in out and "no change in the model detected" in out
    with psycopg.connect(env["owner"]) as c:
        did = c.execute("SELECT id FROM docket.decision WHERE name = 'stream'").fetchone()[0]
        pairs = c.execute("SELECT count(*), count(DISTINCT j2.row_key), bool_and(j1.row_key = j2.row_key AND j1.id < j2.id "
                          "AND j1.input_hash = j2.input_hash AND j1.pair_of IS NULL) FROM docket.judgment j2 "
                          "JOIN docket.judgment j1 ON j1.id = j2.pair_of WHERE j2.decision_id = %s", (did,)).fetchone()
        alarm, r = c.execute("SELECT alarm, result FROM docket.drift_check WHERE kind = 'model' AND decision_id = %s "
                             "ORDER BY id DESC LIMIT 1", (did,)).fetchone()
    assert tuple(pairs) == (60, 60, True)
    assert r["asked_twice"] == 60 and r["probes"] == 60 and r["answers"]["evidence"]["of"] == 0 and not alarm

    FakeSystemOne.answer_override = {"s": "b", "b": "s"}     # the model swaps sales and billing: no one direction
    try:
        code = _drift(env, "--anchors", "30", "--probes", "60", "--twice", "--run")
    finally:
        FakeSystemOne.answer_override = {}
    out = capsys.readouterr().out
    with psycopg.connect(env["owner"]) as c:
        r = c.execute("SELECT result FROM docket.drift_check WHERE kind = 'model' AND decision_id = %s ORDER BY id DESC "
                      "LIMIT 1", (did,)).fetchone()[0]
    e = r["answers"]["evidence"]
    assert code == 1 and "⚠ changed" in out and r["answers"]["alarm"] and r["asked_twice"] == 120
    assert e["odd"] == e["of"] > 20 and e["log_e"] > e["threshold"]            # every odd one out is a stored answer
    assert r["scores"]["up"] == r["scores"]["down"] == 0      # the fake's scores never move: only the answers show it

    code = _drift(env, "--model")                             # the same pairs, reported again: they count once
    with psycopg.connect(env["owner"]) as c:
        again = c.execute("SELECT result FROM docket.drift_check WHERE kind = 'model' AND decision_id = %s ORDER BY id "
                          "DESC LIMIT 1", (did,)).fetchone()[0]
    assert again["asked_twice"] == 120 and again["answers"]["evidence"] == e
    capsys.readouterr()

    # #39: the model keeps every answer, but scores billing tickets lower and sales tickets higher than it did
    ledger.calibrate(env["owner"], "stream")                  # a new series
    FakeSystemOne.score_override = {"b": 0.8, "s": 0.95}
    try:
        code = _drift(env, "--anchors", "30", "--probes", "60", "--twice", "--run")
    finally:
        FakeSystemOne.score_override = {}
    out = capsys.readouterr().out
    with psycopg.connect(env["owner"]) as c:
        r = c.execute("SELECT result FROM docket.drift_check WHERE kind = 'model' AND decision_id = %s ORDER BY id DESC "
                      "LIMIT 1", (did,)).fetchone()[0]
    p = r["twice_scores"]["evidence"]
    assert code == 1 and r["twice_scores"]["alarm"] and not r["answers"]["alarm"] and not r["accuracy"]["alarm"]
    assert r["answers"]["evidence"]["of"] == 0 and p["odd"] == p["of"] > 20 and p["log_e"] > p["threshold"]
    assert "their scores:" in out and "⚠ changed" in out



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


def test_estimate_by_corrects_each_group_with_its_own_labels(env, capsys):
    """Shares by region (#25). In the north the model calls most technical leads billing; in the south its few
    mistakes never touch billing. Each region is corrected with its own labels, the north is flagged, and rows drawn
    within regions count for regions only."""
    from collections import Counter

    from pgdocket import cli, groups
    teams = ["billing", "technical", "sales"]
    rows = []
    for i in range(1, 601):
        region, team = ("north" if i % 3 == 0 else "south"), teams[(i // 3) % 3]
        said = team
        if region == "north" and team == "technical" and i % 5:
            said = "billing"                                   # 80 % of the north's technical leads
        elif region == "south" and team == "technical" and i % 10 == 1:
            said = "sales"
        rows.append((i, said, team, region, "gold" if i % 2 else "basic"))
    with psycopg.connect(env["owner"], autocommit=True) as c:
        c.execute("CREATE TABLE shop.leads (id int PRIMARY KEY, body text, team text, region text, tier text)")
        with c.cursor() as cur:
            cur.executemany("INSERT INTO shop.leads VALUES (%s, %s, %s, %s, %s)", rows)
    ledger.create(env["owner"], "leads", "shop.leads", "id", ["body"], _spec(env["url"]))
    Worker(env["owner"], poll=0.1, log=lambda *a: None).run(once=True)
    ledger.import_labels(env["owner"], "leads", column="team", sample=240, seed="3")
    truth = {g: sum(t == "billing" for _, _, t, r, _ in rows if r == g) / sum(r == g for *_, r, _ in rows)
             for g in ("north", "south")}
    with psycopg.connect(env["owner"]) as c:
        r = groups.estimate_by(c, "leads", "region", "billing", target=0.05)
    by = {g["g"]: g for g in r["groups"]}
    for g in ("north", "south"):                               # the same numbers as estimate --where on that group
        row = ledger.estimate(env["owner"], "leads", "billing", where=f"region = '{g}'")[0]
        assert by[g]["n"] == row["labels"] and by[g]["judged"] == row["judged"]
        assert by[g]["est"]["theta"] * by[g]["judged"] == pytest.approx(row["estimate"], rel=1e-9)
        assert by[g]["ci"][1] * by[g]["judged"] == pytest.approx(row["ci_high"], rel=1e-9)
        assert by[g]["ci"][0] <= truth[g] <= by[g]["ci"][1]
    assert by["north"]["model_share"] - truth["north"] > 0.15                       # the model's systematic error
    assert [f["group"] for f in r["systematic"]] == ["north"]
    plan = {p["group"]: p for p in r["plan"]["groups"]}
    assert plan["north"]["more"] > plan["south"]["more"] and r["plan"]["uniform"] > r["plan"]["need"]

    with psycopg.connect(env["owner"]) as c:                  # draw the north's and south's extra labels
        d, drawn = groups.draw(c, "leads", "region", r["plan"], seed="1")
        random_keys = {k for (k,) in c.execute("SELECT row_key FROM docket.label WHERE decision_id = %s "
                                                "AND sampling = 'random'", (d["id"],))}
    assert Counter(g for _, g, _ in drawn) == {g: p["more"] for g, p in plan.items() if p["more"]}
    assert not random_keys & {k for k, _, _ in drawn} and {how for _, _, how in drawn} == {"at random"}
    path = os.path.join(os.environ.get("TMPDIR", "/tmp"), f"draw-{uuid.uuid4().hex[:6]}.csv")
    cli.main(["estimate", "--dsn", env["owner"], "leads", "--by", "region", "--answer", "billing", "--target", "0.05",
              "--draw", path])
    out = capsys.readouterr().out
    assert "by region" in out and "understates" not in out and "overstates 'billing'" in out
    assert "label leads --csv" in out and "--stratified region" in out
    team = {str(i): t for i, _, t, _, _ in rows}
    with open(path, newline="") as f:
        sheet = list(csv.DictReader(f))
    assert set(sheet[0]) == {"key", "region", "drawn", "body", "label"} and all(s["label"] == "" for s in sheet)
    for s in sheet:
        s["label"] = team[s["key"]]
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(sheet[0]))
        w.writeheader()
        w.writerows(sheet)
    ledger.import_labels(env["owner"], "leads", csv_path=path, stratified="region")
    with psycopg.connect(env["owner"]) as c:
        kinds = dict(c.execute("SELECT sampling, count(*) FROM docket.label WHERE decision_id = %s GROUP BY 1",
                               (d["id"],)).fetchall())
        after = {g["g"]: g["labels"] for g in groups.estimate_by(c, "leads", "region", "billing")["groups"]}
        tier = groups.estimate_by(c, "leads", "tier", "billing")
    assert kinds == {"random": 240, "stratified": len(sheet)}
    assert after == {g: by[g]["n"] + sum(s["region"] == g for s in sheet) for g in ("north", "south")}
    assert sum(g["labels"] for g in tier["groups"]) == 240     # other columns' groups and plain estimates ignore them
    assert ledger.estimate(env["owner"], "leads", "billing")[0]["labels"] == 240

    # How a row was chosen outlives re-labelling: by hand it stays drawn within regions; drawn again within tiers it
    # belongs to both; a row drawn at random stays random.
    key, rkey = sheet[0]["key"], sorted(random_keys)[0]
    manual = os.path.join(os.environ.get("TMPDIR", "/tmp"), f"manual-{uuid.uuid4().hex[:6]}.csv")
    with open(manual, "w", newline="") as f:
        f.write(f"key,label\n{key},{team[key]}\n{rkey},{team[rkey]}\n")
    ledger.import_labels(env["owner"], "leads", csv_path=manual)
    ledger.import_labels(env["owner"], "leads", csv_path=manual, stratified="tier")
    with psycopg.connect(env["owner"]) as c:
        got = dict((k, (s, st)) for k, s, st in c.execute(
            "SELECT row_key, sampling, strata FROM docket.label WHERE decision_id = %s AND row_key = ANY(%s)",
            (d["id"], [key, rkey])))
        with pytest.raises(psycopg.errors.CheckViolation):
            c.execute("UPDATE docket.label SET sampling = 'stratified' WHERE decision_id = %s AND row_key = %s",
                      (d["id"], rkey))
    assert got == {key: ("stratified", ["region", "tier"]), rkey: ("random", None)}


def test_estimate_by_an_unknown_column_says_so(env):
    from pgdocket import cli
    with pytest.raises(SystemExit) as e:
        cli.main(["estimate", "--dsn", env["owner"], "leads", "--by", "regoin", "--answer", "billing"])
    assert "regoin" in str(e.value) and "columns of the source table" in str(e.value)


def test_estimate_weight_reads_rows_drawn_by_weight_back(env, capsys):
    """A share of a sum (--weight amount) and of a join's rows (--weight: how many orders a case has). The SQL sums are
    those `groups.sums` computes from the rows, its permanent random numbers are `groups.prn`'s; labels drawn at random
    can't vouch for a heavy row nobody labelled; `--draw` writes rows drawn by weight, `label` imports them as
    'weighted', and the estimate finds them again by their ranking (#26)."""
    import numpy as np

    from pgdocket import cli, groups
    rng = np.random.default_rng(26)
    teams, N = ["billing", "technical", "sales"], 900
    team = rng.choice(teams, N, p=[0.3, 0.5, 0.2])
    said = np.where(rng.random(N) < 0.85, team, rng.choice(teams, N))
    amount = np.round(np.exp(rng.normal(3, 1.5, N)), 2)
    region = rng.choice(["north", "south"], N)
    orders = rng.integers(0, 4, N)
    heavy = 0
    orders[heavy], team[heavy], said[heavy] = 3000, "billing", "technical"   # ordered 3,000 times; the model misses it
    with psycopg.connect(env["owner"], autocommit=True) as c:
        c.execute("CREATE TABLE shop.cases (id int PRIMARY KEY, body text, team text, region text, amount numeric)")
        c.execute("CREATE TABLE shop.case_orders (case_id int)")
        with c.cursor() as cur:
            cur.executemany("INSERT INTO shop.cases VALUES (%s, %s, %s, %s, %s)",
                            [(i + 1, str(said[i]), str(team[i]), str(region[i]), float(amount[i])) for i in range(N)])
            cur.executemany("INSERT INTO shop.case_orders VALUES (%s)", [(i + 1,) for i in range(N) for _ in range(orders[i])])
    ledger.create(env["owner"], "cases", "shop.cases", "id", ["body"], _spec(env["url"]))
    Worker(env["owner"], poll=0.1, log=lambda *a: None).run(once=True)
    ledger.import_labels(env["owner"], "cases", column="team", sample=200, seed="9")
    fanout = "(SELECT count(*) FROM shop.case_orders o WHERE o.case_id = s.id)"

    def reference(c, by, weight, seed="1"):                   # the same statistics, from the rows themselves
        d = groups.decision(c, "cases")
        rows = c.execute(
            f"SELECT s.id::text, {'s.' + by + '::text' if by else repr(groups.ALL)}, ({weight})::float8, "
            "(v.answer = 'billing')::int, l.label, l.sampling, l.strata FROM shop.cases s "
            "JOIN docket.v_cases v ON v.key = s.id LEFT JOIN docket.label l ON l.decision_id = %s AND l.row_key = s.id::text "
            "WHERE v.status = 'judged'", (d["id"],)).fetchall()
        out = []
        for g in sorted({r[1] for r in rows}):
            rs = [r for r in rows if r[1] == g]
            out.append(groups.sums(g, [r[2] for r in rs], [r[3] for r in rs], [r[4] == "billing" for r in rs],
                                   [r[4] is not None for r in rs],
                                   [r[5] == "random" or (r[5] == "stratified" and by in (r[6] or [])) for r in rs],
                                   [groups.prn(r[0], seed) for r in rs]))
        return d, out

    with psycopg.connect(env["owner"]) as c:
        for by, weight in ((None, "amount"), ("region", "amount"), (None, fanout)):
            d, want = reference(c, by, weight)
            got = groups.group_stats(c, d, by, "billing", weight=weight)
            assert [g["g"] for g in got] == [g["g"] for g in want]
            for a, b in zip(got, want):
                assert set(a) == set(b)
                for k in a:
                    assert a[k] == pytest.approx(b[k], rel=1e-9, abs=1e-9), (by, weight, k)
        u = dict(c.execute("SELECT s.id::text, ((('x' || substr(md5(s.id::text || ':w:' || '1'), 1, 13))::bit(52)"
                           "::bigint)::float8 + 0.5) / 4503599627370496.0 FROM shop.cases s").fetchall())
        assert all(v == pytest.approx(groups.prn(k), rel=1e-15) for k, v in u.items())

        r = groups.estimate_by(c, "cases", None, "billing", weight=fanout)
    g = r["groups"][0]
    truth = sum(o for o, t in zip(orders, team) if t == "billing") / orders.sum()
    # the heavy case is unlabelled, so the usual interval can't be trusted; betting allows for it (#31), and is wide
    assert g["method"] == "random" and g["heaviest"] > 1.96 * g["est"]["normal_se"] and g["interval"] == "betting"
    assert g["ci"][0] <= truth <= g["ci"][1] and g["ci"][1] - g["ci"][0] > 0.5 * g["heaviest"]
    out = groups.render(r)
    assert "the heaviest unlabelled row holds" in out and "Draw by weight" in out and "(betting)" in out

    path = os.path.join(os.environ.get("TMPDIR", "/tmp"), f"weight-{uuid.uuid4().hex[:6]}.csv")
    for _ in range(6):                                         # rounds: draw by weight, label, estimate
        cli.main(["estimate", "--dsn", env["owner"], "cases", "--weight", fanout, "--answer", "billing",
                  "--target", "0.05", "--draw", path])
        printed = capsys.readouterr().out
        with open(path, newline="") as f:
            sheet = list(csv.DictReader(f))
        if not sheet:
            break
        assert "drawn by weight" in printed and f"label cases --csv {path}" in printed and "--stratified" not in printed
        assert set(sheet[0]) == {"key", "drawn", "body", "label"} and {s["drawn"] for s in sheet} == {"by weight"}
        for s in sheet:
            s["label"] = str(team[int(s["key"]) - 1])
        with open(path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(sheet[0]))
            w.writeheader()
            w.writerows(sheet)
        cli.main(["label", "--dsn", env["owner"], "cases", "--csv", path])
        capsys.readouterr()
    with psycopg.connect(env["owner"]) as c:
        r = groups.estimate_by(c, "cases", None, "billing", weight=fanout, target=0.05)
        d = groups.decision(c, "cases")
        kinds = dict(c.execute("SELECT sampling, count(*) FROM docket.label WHERE decision_id = %s GROUP BY 1",
                               (d["id"],)).fetchall())
        drawn_heavy = c.execute("SELECT sampling FROM docket.label WHERE decision_id = %s AND row_key = %s",
                                (d["id"], str(heavy + 1))).fetchone()
    g = r["groups"][0]
    assert g["method"] == "weight" and g["reliable"] and 1.96 * g["est"]["se"] <= 0.05 and r["plan"]["more"] == 0
    # rows labelled at random count too once they're in the run at the top of the ranking: fewer new labels
    assert drawn_heavy == ("weighted",) and kinds["random"] == 200 and 0 < kinds["weighted"] <= g["labels"]
    assert g["labels"] >= groups.PILOT and g["exact_share"] > 0.5            # the heavy case alone is most of it
    assert g["ci"][0] - 0.02 <= truth <= g["ci"][1] + 0.02
    assert ledger.estimate(env["owner"], "cases", "billing")[0]["labels"] == 200   # plain estimates ignore them

    with pytest.raises(SystemExit) as e:
        cli.main(["estimate", "--dsn", env["owner"], "cases", "--weight", "amount - 100", "--answer", "billing"])
    assert "weights must be ≥ 0" in str(e.value)
    with pytest.raises(SystemExit) as e:
        cli.main(["estimate", "--dsn", env["owner"], "cases", "--weight", "amout", "--answer", "billing"])
    assert "amout" in str(e.value) and "--weight" in str(e.value)


def test_estimate_with_two_versions_reads_nested_samples_from_the_ledger(env, capsys):
    """A cheap model on every row and a second version on a sample, with labels inside it (#27): `backfill --sample`
    takes the shared order, labelled rows first; the SQL sums are those `multi.pattern_stats` computes from the rows;
    `estimate --with` reports the estimate and the cheapest mix, and `--draw` writes the next labels in the order."""
    import numpy as np

    from pgdocket import cli, groups, multi
    from pgdocket.tests.fakes import FakeSystemOne
    rng = np.random.default_rng(27)
    teams, N = ["billing", "technical", "sales"], 1200
    team = rng.choice(teams, N, p=[0.3, 0.5, 0.2])
    said = np.where(rng.random(N) < 0.8, team, rng.choice(teams, N))
    with psycopg.connect(env["owner"], autocommit=True) as c:
        c.execute("CREATE TABLE shop.claims (id int PRIMARY KEY, body text, team text)")
        with c.cursor() as cur:
            cur.executemany("INSERT INTO shop.claims VALUES (%s, %s, %s)",
                            [(i + 1, str(said[i]), str(team[i])) for i in range(N)])
    ledger.create(env["owner"], "claims", "shop.claims", "id", ["body"], _spec(env["url"]))
    Worker(env["owner"], poll=0.1, log=lambda *a: None).run(once=True)            # v1 judges every row
    ledger.import_labels(env["owner"], "claims", column="team", sample=150, seed="4")
    v2, _ = ledger.revise(env["owner"], "claims", model="fake-2")
    FakeSystemOne.answer_override = {"s": "b"}               # v2 calls sales tickets billing
    try:
        cli.main(["backfill", "--dsn", env["owner"], "claims", "--version", str(v2), "--sample", "400"])
        assert "400 rows queued for v2" in capsys.readouterr().out
        Worker(env["owner"], poll=0.1, log=lambda *a: None).run(once=True)
    finally:
        FakeSystemOne.answer_override = {}
    with psycopg.connect(env["owner"]) as c:
        d = groups.decision(c, "claims")
        rows = c.execute(
            "SELECT s.id::text, CASE WHEN l.sampling = 'random' THEN (l.label = 'billing')::int END, "
            "(j1.value = 'billing')::int, (j2.value = 'billing')::int FROM shop.claims s "
            "LEFT JOIN docket.label l ON l.decision_id = %(d)s AND l.row_key = s.id::text "
            "LEFT JOIN docket.current c1 ON c1.decision_id = %(d)s AND c1.version = 1 AND c1.row_key = s.id::text "
            "LEFT JOIN docket.judgment j1 ON j1.id = c1.judgment_id "
            "LEFT JOIN docket.current c2 ON c2.decision_id = %(d)s AND c2.version = %(v)s AND c2.row_key = s.id::text "
            "LEFT JOIN docket.judgment j2 ON j2.id = c2.judgment_id", {"d": d["id"], "v": v2}).fetchall()
        got, n = multi.ledger_stats(c, d, [1, v2], "billing")
        r = multi.estimate_with(c, "claims", "billing", [v2], target=0.05, label_cost=0.5)
    col = lambda i: [np.nan if x[i] is None else x[i] for x in rows]
    want = multi.pattern_stats(["labels", "v1", f"v{v2}"], [col(1), col(2), col(3)])
    key = lambda p: p["mask"]
    assert n == N and sorted(got["patterns"], key=key) == sorted(want["patterns"], key=key)
    labelled = {x[0] for x in rows if x[1] is not None}
    sample = {x[0] for x in rows if x[3] is not None}
    assert len(labelled) == 150 and len(sample) == 400 and labelled <= sample              # labels inside the sample
    truth = float(np.mean(team == "billing"))
    est = r["estimate"]
    assert est["reliable"] and est["ci"][0] <= truth <= est["ci"][1] and est["used"] == ["labels", "v1", f"v{v2}"]
    assert {v["version"]: v["rows"] for v in r["versions"]} == {1: N, v2: 400}
    assert r["plan"][0]["cost"] <= min(p["cost"] for p in r["plan"])

    path = os.path.join(os.environ.get("TMPDIR", "/tmp"), f"next-{uuid.uuid4().hex[:6]}.csv")
    cli.main(["estimate", "--dsn", env["owner"], "claims", "--answer", "billing", "--with", str(v2),
              "--target", "0.03", "--label-cost", "0.5", "--draw", path])
    out = capsys.readouterr().out
    assert "estimate, every source together" in out and "the cheapest mix for ±3.0 points" in out
    assert f"label claims --csv {path} --random" in out
    with open(path, newline="") as f:
        sheet = list(csv.DictReader(f))
    assert sheet and {s["drawn"] for s in sheet} == {"at random"} and not {s["key"] for s in sheet} & labelled
    with psycopg.connect(env["owner"]) as c:
        first = [k for (k,) in c.execute("SELECT s.id::text FROM shop.claims s LEFT JOIN docket.label l "
                                         "ON l.decision_id = %s AND l.row_key = s.id::text AND l.sampling = 'random' "
                                         "WHERE l.row_key IS NULL ORDER BY md5(s.id::text || ':j:1') LIMIT %s",
                                         (d["id"], len(sheet)))]
    assert [s["key"] for s in sheet] == first                     # the next in the order: inside every later sample
    for s in sheet:
        s["label"] = str(team[int(s["key"]) - 1])
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(sheet[0]))
        w.writeheader()
        w.writerows(sheet)
    ledger.import_labels(env["owner"], "claims", csv_path=path, random=True)
    assert ledger.estimate(env["owner"], "claims", "billing")[0]["labels"] == 150 + len(sheet)

    with pytest.raises(SystemExit) as e:
        cli.main(["estimate", "--dsn", env["owner"], "claims", "--answer", "billing", "--with", "1", "--weight", "1"])
    assert "takes one version other than the active one" in str(e.value)


def test_estimate_with_by_group_queues_each_groups_sample_and_draws_its_labels(env, capsys):
    """--with and --by together (#28): each group from its own labels and versions' samples. `--queue` has a version
    judge the first rows of each group's shared order, `--draw` writes the next labels in each group (a pilot where a
    group has too few), and once they are labelled (`label --stratified`) every group's labels lie inside its sample.
    The SQL sums by group are those `multi.pattern_stats` computes from the rows."""
    import numpy as np

    from pgdocket import cli, groups, multi
    from pgdocket.tests.fakes import FakeSystemOne
    rng = np.random.default_rng(28)
    teams, N = ["billing", "technical", "sales"], 1200
    site = rng.choice(["north", "south", "west"], N, p=[0.5, 0.35, 0.15])
    team = rng.choice(teams, N, p=[0.3, 0.5, 0.2])
    said = np.where(rng.random(N) < 0.8, team, rng.choice(teams, N))
    with psycopg.connect(env["owner"], autocommit=True) as c:
        c.execute("CREATE TABLE shop.tix (id int PRIMARY KEY, body text, team text, site text)")
        with c.cursor() as cur:
            cur.executemany("INSERT INTO shop.tix VALUES (%s, %s, %s, %s)",
                            [(i + 1, str(said[i]), str(team[i]), str(site[i])) for i in range(N)])
    FakeSystemOne.answer_override = {"s": "b"}               # v1, the active version, calls sales tickets billing
    try:
        ledger.create(env["owner"], "tix", "shop.tix", "id", ["body"], _spec(env["url"]))
        Worker(env["owner"], poll=0.1, log=lambda *a: None).run(once=True)
    finally:
        FakeSystemOne.answer_override = {}
    ledger.import_labels(env["owner"], "tix", column="team", sample=100, seed="4")
    v2, _ = ledger.revise(env["owner"], "tix", model="fake-2")
    cli.main(["backfill", "--dsn", env["owner"], "tix", "--version", str(v2), "--sample", "300"])
    Worker(env["owner"], poll=0.1, log=lambda *a: None).run(once=True)
    capsys.readouterr()
    with psycopg.connect(env["owner"]) as c:
        d = groups.decision(c, "tix")
        r = multi.estimate_with(c, "tix", "billing", [v2], target=0.08, label_cost=0.5, costs={v2: 0.01}, by="site")
    plans = {x["g"]: x["plan"][0] for x in r["groups"]}
    assert set(plans) == {"north", "south", "west"} and plans["west"]["kind"] == "pilot"
    assert plans["north"]["models"][0] == f"v{v2}" and plans["north"]["rows"][f"v{v2}"] < 600   # v2 on a sample
    more = sum(p["more_rows"].get(f"v{v2}", 0) for p in plans.values())
    path = os.path.join(os.environ.get("TMPDIR", "/tmp"), f"next-{uuid.uuid4().hex[:6]}.csv")
    cli.main(["estimate", "--dsn", env["owner"], "tix", "--answer", "billing", "--with", str(v2), "--by", "site",
              "--target", "0.08", "--label-cost", "0.5", "--cost", f"{v2}=0.01", "--queue", "--draw", path])
    out = capsys.readouterr().out
    assert "by site" in out and "the cheapest mix for ±8.0 points in every group" in out and "pilot" in out
    assert f"{more:,} rows queued: v{v2}'s sample in each site" in out and more > 0
    assert f"label tix --csv {path} --stratified site" in out
    with open(path, newline="") as f:
        sheet = list(csv.DictReader(f))
    assert {s["drawn"] for s in sheet} == {"at random"}
    assert {g: sum(s["site"] == g for s in sheet) for g in plans} == {g: p["more_labels"] for g, p in plans.items()}
    with psycopg.connect(env["owner"]) as c:
        for g in plans:                                       # the next rows of each group's order
            first = [k for (k,) in c.execute(
                "SELECT s.id::text FROM shop.tix s LEFT JOIN docket.label l ON l.decision_id = %s "
                "AND l.row_key = s.id::text WHERE s.site = %s AND l.row_key IS NULL ORDER BY md5(s.id::text || ':j:1') "
                "LIMIT %s", (d["id"], g, plans[g]["more_labels"]))]
            assert [s["key"] for s in sheet if s["site"] == g] == first
    Worker(env["owner"], poll=0.1, log=lambda *a: None).run(once=True)
    for s in sheet:
        s["label"] = str(team[int(s["key"]) - 1])
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(sheet[0]))
        w.writeheader()
        w.writerows(sheet)
    ledger.import_labels(env["owner"], "tix", csv_path=path, stratified="site")

    with psycopg.connect(env["owner"]) as c:
        rows = c.execute(
            "SELECT s.id::text, s.site, CASE WHEN l.sampling = 'random' OR (l.sampling = 'stratified' AND "
            "'site' = ANY(l.strata)) THEN (l.label = 'billing')::int END, (j1.value = 'billing')::int, "
            "(j2.value = 'billing')::int FROM shop.tix s "
            "LEFT JOIN docket.label l ON l.decision_id = %(d)s AND l.row_key = s.id::text "
            "LEFT JOIN docket.current c1 ON c1.decision_id = %(d)s AND c1.version = 1 AND c1.row_key = s.id::text "
            "LEFT JOIN docket.judgment j1 ON j1.id = c1.judgment_id "
            "LEFT JOIN docket.current c2 ON c2.decision_id = %(d)s AND c2.version = %(v)s AND c2.row_key = s.id::text "
            "LEFT JOIN docket.judgment j2 ON j2.id = c2.judgment_id", {"d": d["id"], "v": v2}).fetchall()
        got = multi.ledger_stats(c, d, [1, v2], "billing", by="site")
        r = multi.estimate_with(c, "tix", "billing", [v2], target=0.08, label_cost=0.5, costs={v2: 0.01}, by="site")
    key = lambda p: p["mask"]
    for g, st, n in got:
        mine = [x for x in rows if x[1] == g]
        col = lambda i: [np.nan if x[i] is None else x[i] for x in mine]
        want = multi.pattern_stats(["labels", "v1", f"v{v2}"], [col(2), col(3), col(4)])
        assert n == len(mine) and sorted(st["patterns"], key=key) == sorted(want["patterns"], key=key)
        labelled = {x[0] for x in mine if x[2] is not None}
        assert labelled <= {x[0] for x in mine if x[4] is not None}              # labels inside v2's sample
        assert len(labelled) >= (groups.PILOT if g == "west" else plans[g]["labels"])
    for x in r["groups"]:
        truth = float(np.mean(team[site == x["g"]] == "billing"))
        e = x["estimate"]
        assert e["theta"] is not None and e["ci"][0] - 0.02 <= truth <= e["ci"][1] + 0.02
    whole = float(np.mean(team == "billing"))
    assert r["overall"]["ci"][0] - 0.02 <= whole <= r["overall"]["ci"][1] + 0.02
    assert ledger.estimate(env["owner"], "tix", "billing")[0]["labels"] == 100        # plain estimates: random only


def test_estimate_with_weight_runs_v_and_the_labels_down_one_ranking_by_weight(env, capsys):
    """--with and --weight together (#29): labels and V take nested runs of one ranking by u / w, the active version
    judges every row. `--queue` has V judge its run and `--draw` writes the labels' run, a pilot first and then the
    cheapest thresholds; the SQL sums are those `multiweight.sums` computes from the rows, and the estimate holds."""
    import numpy as np

    from pgdocket import cli, groups, multiweight
    from pgdocket.tests.fakes import FakeSystemOne
    rng = np.random.default_rng(29)
    teams, N = ["billing", "technical", "sales"], 800
    team = rng.choice(teams, N, p=[0.3, 0.5, 0.2])
    said = np.where(rng.random(N) < 0.85, team, rng.choice(teams, N))
    copies = (np.floor(rng.pareto(1.2, N) * 2) + 1).astype(int)
    copies[:3] = [900, 400, 250]                          # a few texts a join counts hundreds of times
    site = rng.choice(["north", "south"], N, p=[0.6, 0.4])
    with psycopg.connect(env["owner"], autocommit=True) as c:
        c.execute("CREATE TABLE shop.texts (id int PRIMARY KEY, body text, team text, copies int, site text)")
        with c.cursor() as cur:
            cur.executemany("INSERT INTO shop.texts VALUES (%s, %s, %s, %s, %s)",
                            [(i + 1, str(said[i]), str(team[i]), int(copies[i]), str(site[i])) for i in range(N)])
    FakeSystemOne.answer_override = {"s": "b"}               # v1, the active version, calls sales texts billing
    try:
        ledger.create(env["owner"], "texts", "shop.texts", "id", ["body"], _spec(env["url"]))
        Worker(env["owner"], poll=0.1, log=lambda *a: None).run(once=True)
    finally:
        FakeSystemOne.answer_override = {}
    v2, _ = ledger.revise(env["owner"], "texts", model="fake-2")
    truth = float((copies * (team == "billing")).sum() / copies.sum())
    path = os.path.join(os.environ.get("TMPDIR", "/tmp"), f"next-{uuid.uuid4().hex[:6]}.csv")
    args = ["estimate", "--dsn", env["owner"], "texts", "--answer", "billing", "--with", str(v2), "--weight", "copies",
            "--target", "0.025", "--label-cost", "0.5", "--cost", f"{v2}=0.01", "--queue", "--draw", path]
    kinds = []
    for _ in range(4):                                     # rounds: queue V's run, label the drawn rows, again
        capsys.readouterr()
        cli.main(args)
        out = capsys.readouterr().out
        kinds.append(next(k for k in ("nothing more", "pilot", "label every row", f"labels, v{v2}", "labels and v1")
                          if k in out.split("the cheapest mix")[1]))
        if "rows queued" in out:
            assert f"rows queued for v{v2}: its run of the ranking by weight" in out
            Worker(env["owner"], poll=0.1, log=lambda *a: None).run(once=True)
        if "rows to label written" not in out:
            break
        assert f"pgdocket label texts --csv {path}" in out
        with open(path, newline="") as f:
            sheet = list(csv.DictReader(f))
        assert sheet and {s["drawn"] for s in sheet} == {"by weight"}
        for s in sheet:
            s["label"] = str(team[int(s["key"]) - 1])
        with open(path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(sheet[0]))
            w.writeheader()
            w.writerows(sheet)
        cli.main(["label", "--dsn", env["owner"], "texts", "--csv", path])
        assert f"{len(sheet)} labels imported for texts (drawn by weight)" in capsys.readouterr().out
    assert kinds[0] == "pilot" and f"labels, v{v2}" in kinds and kinds[-1] == "nothing more"
    with psycopg.connect(env["owner"]) as c:
        d = groups.decision(c, "texts")
        got = multiweight.ledger_sums(c, d, v2, "billing", weight="copies")[0]
        rows = c.execute(
            "SELECT s.id, (j1.value = 'billing')::int, (j2.value = 'billing')::int, (l.label = 'billing')::int "
            "FROM shop.texts s JOIN docket.current c1 ON c1.decision_id = %(d)s AND c1.version = 1 "
            "AND c1.row_key = s.id::text JOIN docket.judgment j1 ON j1.id = c1.judgment_id "
            "LEFT JOIN docket.current c2 ON c2.decision_id = %(d)s AND c2.version = %(v)s AND c2.row_key = s.id::text "
            "LEFT JOIN docket.judgment j2 ON j2.id = c2.judgment_id "
            "LEFT JOIN docket.label l ON l.decision_id = %(d)s AND l.row_key = s.id::text ORDER BY s.id",
            {"d": d["id"], "v": v2}).fetchall()
        r = multiweight.estimate_with(c, "texts", "billing", [v2], "copies")
        by = multiweight.ledger_sums(c, d, v2, "billing", weight="copies", by="site")
    ids = np.array([x[0] for x in rows])
    col = lambda i: np.array([np.nan if x[i] is None else x[i] for x in rows], float)
    want = multiweight.sums(groups.ALL, copies[ids - 1], col(1), col(2), col(3),
                            [groups.prn(str(i)) for i in ids])
    for key in ("rows", "weight", "w2", "xi0", "xi1", "c_n", "c_wy", "wr", "wra", "run_all", "vjudged"):
        assert got[key] == pytest.approx(want[key]), key
    for part in ("L0", "Q0", "L01", "Q01", "run"):
        assert got[part] == pytest.approx(want[part]), part
    assert got["xi1"] >= got["xi0"] and got["c_n"] >= 1          # labels inside V's run; the heaviest text certain
    for g in by:                                                 # by group: each group's run of the same ranking
        m = site[ids - 1] == g["g"]
        mine = multiweight.sums(g["g"], copies[ids - 1][m], col(1)[m], col(2)[m], col(3)[m],
                                [groups.prn(str(i)) for i in ids[m]])
        for key in ("rows", "weight", "xi0", "xi1", "c_wy", "wr"):
            assert g[key] == pytest.approx(mine[key]), (g["g"], key)
        for part in ("L0", "L01", "run"):
            assert g[part] == pytest.approx(mine[part]), (g["g"], part)
    capsys.readouterr()
    cli.main(["estimate", "--dsn", env["owner"], "texts", "--answer", "billing", "--with", str(v2), "--weight", "copies",
              "--by", "site"])
    out = capsys.readouterr().out
    assert ", by site, from labels drawn by weight" in out and "north" in out and "south" in out
    e = r["groups"][0]["est"]
    assert e["used"] == ["labels", "v1", f"v{v2}"] and e["ci"][0] - 0.02 <= truth <= e["ci"][1] + 0.02
    assert e["labels"] < N / 2                                   # far from labelling every text

    import math
    with psycopg.connect(env["owner"], autocommit=True) as c:    # a plan's "every row": V's cut-off at ∞
        queued = multiweight.queue(c, d, v2, [(groups.ALL, math.inf)], weight="copies")
    assert queued == N - got["vjudged"]
    Worker(env["owner"], poll=0.1, log=lambda *a: None).run(once=True)
    with psycopg.connect(env["owner"]) as c:
        every = multiweight.ledger_sums(c, d, v2, "billing", weight="copies")[0]
        r = multiweight.estimate_with(c, "texts", "billing", [v2], "copies")
    assert every["vjudged"] == N and math.isinf(every["xi1"]) and every["run"]["D"] == 0     # V a census: no 1 − π
    e = r["groups"][0]["est"]
    assert e["used"] == ["labels", "v1", f"v{v2}"] and e["ci"][0] - 0.02 <= truth <= e["ci"][1] + 0.02


def test_a_rare_answer_below_the_floor_gets_a_betting_interval(env, capsys):
    """2 % of 3,000 rows are billing, and 150 labels drawn at random hold about 3 of them: fewer than the 10 the usual
    interval needs (#30). `estimate` and `estimate --by` give a betting interval instead, from the labels in the order
    they were drawn, and it holds; the plan for ± 3 points takes betting's labels, far below the floor's."""
    import numpy as np

    from pgdocket import betting, cli, groups
    rng = np.random.default_rng(30)
    N = 3000
    team = rng.choice(["billing", "technical", "sales"], N, p=[0.02, 0.68, 0.30])
    said = np.where(rng.random(N) < 0.9, team, rng.choice(["billing", "technical", "sales"], N))
    region = rng.choice(["north", "south"], N, p=[0.6, 0.4])
    with psycopg.connect(env["owner"], autocommit=True) as c:
        c.execute("CREATE TABLE shop.notes (id int PRIMARY KEY, body text, team text, region text)")
        with c.cursor() as cur:
            cur.executemany("INSERT INTO shop.notes VALUES (%s, %s, %s, %s)",
                            [(i + 1, str(said[i]), str(team[i]), str(region[i])) for i in range(N)])
    ledger.create(env["owner"], "notes", "shop.notes", "id", ["body"], _spec(env["url"]))
    Worker(env["owner"], poll=0.1, log=lambda *a: None).run(once=True)
    ledger.import_labels(env["owner"], "notes", column="team", sample=150, seed="30")

    capsys.readouterr()
    cli.main(["estimate", "--dsn", env["owner"], "notes", "--answer", "billing"])
    out = capsys.readouterr().out
    line = next(x for x in out.splitlines() if x.startswith("billing"))
    assert "(betting)" in line and "⚠" not in out and "valid at any number of labels" in out
    lo, hi = (float(x.replace(",", "")) for x in line.split()[3:6:2])
    truth = int((team == "billing").sum())
    assert lo <= truth <= hi

    with psycopg.connect(env["owner"]) as c:
        d = groups.decision(c, "notes")
        rows = groups.labelled_rows(c, d, None, "billing")[groups.ALL]
        order = [k for (k,) in c.execute("SELECT row_key FROM docket.label WHERE decision_id = %s "
                                         "ORDER BY labeled_at, md5(row_key)", (d["id"],))]
        r = groups.estimate_by(c, "notes", "region", "billing", target=0.03)
    y = np.array([team[int(k) - 1] == "billing" for k in order], float)
    assert len(rows[0]) == 150 and (rows[0] == y).all()                 # the labels in the order they were drawn
    for g in r["groups"]:
        share = float(np.mean(team[region == g["g"]] == "billing"))
        assert g["interval"] == "betting" and g["reliable"] and g["ci"][0] <= share <= g["ci"][1]
    p = {x["group"]: x for x in r["plan"]["groups"]}
    for g in r["groups"]:
        floor = 10 / max(g["est"]["theta"], 1 / (g["n"] + 2))
        assert p[g["g"]]["need"] < 0.75 * floor                          # betting's labels, not the floor's

    # docket.estimate() in SQL gives the same betting interval itself (#32); not from labels chosen by hand
    s = ledger.estimate(env["owner"], "notes", "billing")[0]
    lo, hi = betting.interval(*rows, s["model_count"], s["judged"])
    assert s["method"] == "betting" and s["reliable"] and s["note"] is None
    assert s["ci_low"] == pytest.approx(lo * s["judged"], abs=1e-9) and s["ci_high"] == pytest.approx(hi * s["judged"], abs=1e-9)
    assert s["ci_low"] <= s["estimate"] <= s["ci_high"]
    assert ledger.estimate(env["owner"], "notes", "billing", include_manual=True)[0]["method"] == "normal"


def test_weighted_and_several_model_estimates_below_their_floors_bet(env, capsys):
    """2 % of 2,000 parcels are billing (#31). Labels drawn by weight (a join's fan-out) hold fewer than 5 billing rows:
    `estimate --weight` reads the run and the rest back from SQL as `groups.run_rows` computes them from the rows, and
    gives a betting interval that holds, as `estimate --with 2 --weight` does; 150 labels drawn at random hold fewer
    than 10: `estimate --with 2` bets with the labels and v1, which judged every row, in the order the labels came."""
    import numpy as np

    from pgdocket import cli, groups, multi, multiweight
    rng = np.random.default_rng(31)
    teams, N = ["billing", "technical", "sales"], 2000
    team = rng.choice(teams, N, p=[0.02, 0.68, 0.30])
    said = np.where(rng.random(N) < 0.9, team, rng.choice(teams, N))
    orders = np.minimum(np.floor(rng.pareto(1.3, N)).astype(int) + 1, 300)
    with psycopg.connect(env["owner"], autocommit=True) as c:
        c.execute("CREATE TABLE shop.parcels (id int PRIMARY KEY, body text, team text)")
        c.execute("CREATE TABLE shop.parcel_orders (parcel_id int)")
        with c.cursor() as cur:
            cur.executemany("INSERT INTO shop.parcels VALUES (%s, %s, %s)",
                            [(i + 1, str(said[i]), str(team[i])) for i in range(N)])
            cur.executemany("INSERT INTO shop.parcel_orders VALUES (%s)",
                            [(i + 1,) for i in range(N) for _ in range(orders[i])])
    ledger.create(env["owner"], "parcels", "shop.parcels", "id", ["body"], _spec(env["url"]))
    Worker(env["owner"], poll=0.1, log=lambda *a: None).run(once=True)
    ledger.import_labels(env["owner"], "parcels", column="team", sample=150, seed="31")
    fanout = "(SELECT count(*) FROM shop.parcel_orders o WHERE o.parcel_id = s.id)"
    keys = [str(i + 1) for i in range(N)]
    u = np.array([groups.prn(k) for k in keys])
    w, y = orders.astype(float), (team == "billing").astype(float)
    with psycopg.connect(env["owner"]) as c:
        d = groups.decision(c, "parcels")
        random_keys = {k for (k,) in c.execute("SELECT row_key FROM docket.label WHERE decision_id = %s", (d["id"],))}
        f = np.array([a == "billing" for (a,) in c.execute(
            "SELECT v.answer FROM shop.parcels s JOIN docket.v_parcels v ON v.key = s.id ORDER BY s.id")], float)
    path = os.path.join(os.environ.get("TMPDIR", "/tmp"), f"run-{uuid.uuid4().hex[:6]}.csv")
    with open(path, "w", newline="") as fh:                  # the first 60 rows of the ranking by weight, labelled
        out = csv.writer(fh)
        out.writerow(["key", "drawn", "body", "label"])
        for i in np.argsort(u / w)[:60]:
            if keys[i] not in random_keys:
                out.writerow([keys[i], "by weight", said[i], team[i]])
    cli.main(["label", "--dsn", env["owner"], "parcels", "--csv", path])
    truth = float((w * y).sum() / w.sum())
    with psycopg.connect(env["owner"]) as c:
        r = groups.estimate_by(c, "parcels", None, "billing", weight=fanout, target=0.02)
        g = r["groups"][0]
        got = groups.drawn_rows(c, d, None, "billing", None, fanout, {groups.ALL: g["p_xi"]})[groups.ALL]
        mw = multiweight.estimate_with(c, "parcels", "billing", [ledger.revise(env["owner"], "parcels", model="fake-2")[0]],
                                       fanout)
        with_ = multi.estimate_with(c, "parcels", "billing", [mw["versions"][1]["version"]])
        rows = multi.labelled_rows(c, d, [1], "billing")[groups.ALL]
        order = [k for (k,) in c.execute("SELECT row_key FROM docket.label WHERE decision_id = %s AND sampling = 'random' "
                                         "ORDER BY labeled_at, md5(row_key)", (d["id"],))]
    assert g["method"] == "weight" and min(g["effective"]) < groups.DRAWN_EACH_SIDE
    want = groups.run_rows(w, f, y, u, g["p_xi"])
    for k in ("y", "f", "w", "u"):
        assert np.allclose(got[k], want[k], rtol=1e-12), k
    assert np.allclose(np.sort(got["heavy"]), np.sort(want["heavy"])) and np.allclose(got["powers"], want["powers"])
    assert g["interval"] == "betting" and g["reliable"] and g["ci"][0] <= truth <= g["ci"][1]
    p = r["plan"]["groups"][0]
    assert p["kind"] in ("target", "exact")
    e = mw["groups"][0]["est"]
    assert e["interval"] == "betting" and e["ci"] == g["ci"] and "(betting)" in multiweight.render(mw)
    e = with_["estimate"]
    share = float(y.mean())
    assert e["interval"] == "betting" and e["bet_with"] == "v1" and e["ci"][0] <= share <= e["ci"][1]
    assert list(rows[0]) == [float(team[int(k) - 1] == "billing") for k in order]   # the labels in the order drawn
    capsys.readouterr()
    cli.main(["estimate", "--dsn", env["owner"], "parcels", "--weight", fanout, "--answer", "billing"])
    out = capsys.readouterr().out
    assert "(betting)" in out and f"fewer than {groups.DRAWN_EACH_SIDE} rows drawn by weight" in out and "⚠" not in out
    cli.main(["estimate", "--dsn", env["owner"], "parcels", "--answer", "billing", "--with", "2"])
    out = capsys.readouterr().out
    assert "(betting)" in out and "v1, which judged every row" in out and "⚠" not in out


def test_sql_betting_interval_is_the_pythons(env):
    """docket.betting_interval finds the ends of the betting interval by bisection, pgdocket.betting.interval over every
    candidate share (#32): on random labels, few or many, rare answers or common, for scopes of 40 rows to a million
    (every share k / N a candidate, or 4,001 evenly spaced), both give the same interval, or both none."""
    import numpy as np

    from pgdocket import betting
    rng = np.random.default_rng(32)
    cases = []
    for _ in range(60):
        N = int(rng.choice([40, 300, 2000, 4000, 4001, 20000, 1_000_000]))
        n = int(min(N - 1, rng.choice([1, 5, 20, 60, 150, 400])))
        share = float(rng.choice([0.002, 0.01, 0.03, 0.1, 0.3, 0.6]))
        y = (rng.random(n) < share).astype(float)
        f = np.where(rng.random(n) < float(rng.choice([0.6, 0.9, 0.99])), y, 1 - y)
        cases.append((y, f, max(float(f.sum()), round(N * share)), N, float(rng.choice([0.05, 0.1]))))
    cases.append((np.r_[np.ones(5), np.zeros(45)], np.r_[np.ones(5), np.zeros(45)], 5.0, 50, 0.05))   # all but none
    cases.append((np.ones(4), np.ones(4), 4.0, 4, 0.05))                                               # every row
    with psycopg.connect(env["owner"]) as c:
        for y, f, total, N, alpha in cases:
            want = betting.interval(y, f, total, N, alpha)
            got = c.execute("SELECT docket.betting_interval(%s::float8[], %s::float8[], %s, %s, %s)",
                            ([float(x) for x in y], [float(x) for x in f], total, N, alpha)).fetchone()[0]
            assert (got is None) == (want is None), (N, len(y))
            if want is not None:
                assert got == pytest.approx(list(want), abs=1e-12), (N, len(y), want, got)
