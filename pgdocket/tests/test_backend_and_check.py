import numpy as np
import pytest

from pgdocket.backend import Backend, BackendError
from pgdocket.check import CheckConfig, analyse_choice, analyse_noul, render
from pgdocket.tests.fakes import FakeSystemOne, serve


@pytest.fixture()
def server():
    url, shutdown = serve()
    FakeSystemOne.fail_first = True
    yield url
    shutdown()


def test_backend_batches_keeps_order_and_retries(server):
    b = Backend(server, model="fake", concurrency=4)
    rows = [{"text": f"row {i}", "want": ["a", "b", "c"][i % 3]} for i in range(45)]
    answers = b.judge(rows, "choice", "which letter?", ["a: first", "b: second", "c: third"], batch_size=20)
    assert [a["choice"][0] for a in answers] == [r["want"] for r in rows]
    assert sorted(len(r["state"]["rows"]) for r in FakeSystemOne.requests) == [5, 20, 20]
    assert b.stats["requests"] == 3 and b.stats["input_tokens"] == 4500 and b.stats["retries"] == 1
    first = FakeSystemOne.requests[0]["questions"]["r0"]
    assert first["instructions"].startswith("For the record `rows[0]`") and "a: first" in first["criteria"]


def test_backend_reuses_connections_across_calls(server):
    FakeSystemOne.fail_first = False
    b = Backend(server, model="fake", concurrency=4)
    rows = [{"text": f"row {i}", "want": "a"} for i in range(40)]
    for _ in range(3):                                            # three separate calls, 120 requests in total
        b.judge(rows, "choice", "which?", ["a", "b"], batch_size=1)
    assert len(FakeSystemOne.requests) == 120
    assert len(set(FakeSystemOne.ports)) <= 4                     # at most one connection per pool thread


def test_backend_requires_key_for_typesafe():
    with pytest.raises(BackendError):
        Backend("jev", api_key=None)


def _cfg(kind, **kw):
    base = dict(dsn="", table="demo.t", key="id", text=["body"], kind=kind, question="q?", label_column="label",
                precision_target=0.8)
    base.update(kw)
    return CheckConfig(**base)


def test_choice_analysis_and_report():
    rng = np.random.default_rng(3)
    cfg = _cfg("choice", options=[{"name": n, "description": d} for n, d in [("a", "first"), ("b", "second"), ("c", "third")]])
    display = ["a: first", "b: second", "c: third"]
    sample, answers = [], []
    for i in range(400):
        label = "abc"[i % 3]
        right = rng.random() < 0.8
        pick = label if right else "abc"[(i + 1) % 3]
        probs = {d: (0.85 if d[0] == pick else 0.075) for d in display}     # over-confident: 85 % vs 80 % right
        sample.append({"key": str(i), "record": {"body": "SECRET-ROW-TEXT"}, "label": label})
        answers.append({"type": "choice", "choice": display["abc".index(pick)], "probabilities": probs})
    a = analyse_choice(cfg, sample, answers)
    assert 0.75 < a["accuracy"] < 0.85
    assert a["threshold"]["tested"]
    a.pop("_top")
    result = {"config": {**cfg.__dict__}, "analysis": a, "stability": {"rows": 50, "changed": 0.04},
              "batching": {"rows": 50, "changed": 0.12, "accuracy_single": 0.8, "accuracy_batched": 0.74},
              "backend": {"url": "fake", "model": "m", "batch_size": 1}, "created_at": "now", "docket_version": "0",
              "sample": {"labeled_rows": 400, "rows_in_scope": 1000, "table_rows": 2000, "label_counts": {}},
              "cost": {"requests": 400, "input_tokens": 40000, "usd": 0.0017, "tokens_per_row": 100,
                       "projected_usd_table": 0.0084, "projected_usd_per_million_rows": 4.2, "seconds": 3}}
    md = render(result)
    assert "Judge one row per request" in md and "Store answers" in md
    assert "SECRET-ROW-TEXT" not in md                   # the report never contains row text


def test_noul_analysis_threshold_has_recall():
    rng = np.random.default_rng(4)
    cfg = _cfg("noul", positive="yes")
    sample, answers = [], []
    for i in range(500):
        yes = rng.random() < 0.3
        p = float(np.clip(rng.normal(0.75 if yes else 0.3, 0.15), 0.01, 0.99))
        sample.append({"key": str(i), "record": {"body": "x"}, "label": "yes" if yes else "no"})
        answers.append({"type": "noul", "noul": p})
    a = analyse_noul(cfg, sample, answers)
    assert a["auroc"] > 0.9
    assert a["threshold"]["chosen"] and 0 < a["threshold"]["chosen"]["recall"] <= 1
    rl = {r["target"]: r for r in a["recall_ladder"]}
    assert set(rl) == {0.95, 0.9, 0.8} and rl[0.8]["chosen"]["dismissed"] >= rl[0.95]["chosen"]["dismissed"]
    a.pop("_p")
    md = render({"config": {**cfg.__dict__}, "analysis": a, "backend": {"url": "fake", "model": "m", "batch_size": 1},
                 "created_at": "now", "docket_version": "0",
                 "sample": {"labeled_rows": 500, "rows_in_scope": 500, "table_rows": 500, "label_counts": {}},
                 "cost": {"requests": 500, "input_tokens": 50000, "usd": 0.002, "tokens_per_row": 100,
                          "projected_usd_table": 0.002, "projected_usd_per_million_rows": 4.2, "seconds": 3}})
    assert "What each recall target allows" in md and "- This check: 500 requests" in md


def test_recommendation_when_calibration_does_not_help_yet():
    from pgdocket.check import recommendations
    r = {"config": {"kind": "choice"}, "sample": {"labeled_rows": 60}, "stability": None, "batching": None,
         "analysis": {"ece_raw": 0.052, "ece_calibrated": 0.055,
                      "threshold": {"chosen": None, "target": 0.9, "alpha": 0.05}, "ladder": []}}
    text = " ".join(recommendations(r))
    assert "doesn't improve it yet" in text and "Don't read the raw scores" not in text


def test_check_reports_the_threshold_calibrate_would_store():
    """`check` and `calibrate` choose the guaranteed threshold the same way, on the model's own score (#16)."""
    from pgdocket import calibration
    rng = np.random.default_rng(6)
    opts = [{"name": n} for n in "abc"]
    sample, answers = [], []
    for i in range(300):
        label, s = "abc"[i % 3], round(float(rng.uniform(0.4, 1.0)), 2)
        pick = label if rng.random() < 0.3 + 0.7 * s else "abc"[(i + 1) % 3]
        sample.append({"key": str(i), "record": None, "label": label})
        answers.append({"choice": pick, "probabilities": {n: (s if n == pick else (1 - s) / 2) for n in "abc"}})
    a = analyse_choice(_cfg("choice", options=opts, precision_target=0.85), sample, answers)
    cal = calibration.fit({"kind": "choice", "question": "q?", "options": opts}, answers,
                          [s["label"] for s in sample], precision_target=0.85)
    assert a["threshold_on"] == cal["threshold_on"] == "raw score"
    assert a["threshold"]["chosen"]["threshold"] == cal["threshold"] and a["threshold"]["chosen"]["coverage"] == cal["coverage"]

    p = [float(np.clip(rng.normal(0.7 if i % 3 == 0 else 0.3, 0.15), 0.01, 0.99)) for i in range(300)]
    yes = [i % 3 == 0 for i in range(300)]
    a = analyse_noul(_cfg("noul", positive="yes", precision_target=0.85),
                     [{"key": str(i), "record": None, "label": "yes" if y else "no"} for i, y in enumerate(yes)],
                     [{"noul": x} for x in p])
    cal = calibration.fit({"kind": "noul", "question": "q?"}, [{"noul": x} for x in p], yes, precision_target=0.85)
    assert a["threshold"]["chosen"]["threshold"] == cal["threshold"]


def test_check_names_answers_below_target_and_reports_them_per_answer():
    rng = np.random.default_rng(8)
    opts = [{"name": n} for n in "abc"]
    sample, answers = [], []
    for i in range(400):
        pick = "abc"[i % 10 // 4] if i % 10 < 8 else "c"                 # c is answered 20 % of the time...
        right = rng.random() < (0.97 if pick != "c" else 0.45)           # ...and is often wrong
        label = pick if right else ("a" if pick != "a" else "b")
        sample.append({"key": str(i), "record": None, "label": label})
        answers.append({"choice": pick, "probabilities": {n: (0.95 if n == pick else 0.025) for n in "abc"}})
    cfg = _cfg("choice", options=opts, precision_target=0.8)
    a = analyse_choice(cfg, sample, answers)
    weak = [p["answer"] for p in a["per_answer"] if p["below_target"]]
    assert a["threshold"]["chosen"] and weak == ["c"]
    a.pop("_top")
    result = {"config": {**cfg.__dict__}, "analysis": a, "backend": {"url": "fake", "model": "m", "batch_size": 1},
              "created_at": "now", "docket_version": "0",
              "sample": {"labeled_rows": 400, "rows_in_scope": 400, "table_rows": 400, "label_counts": {}},
              "cost": {"requests": 400, "input_tokens": 40000, "usd": 0.0017, "tokens_per_row": 100,
                       "projected_usd_table": 0.0017, "projected_usd_per_million_rows": 4.2, "seconds": 3}}
    md = render(result)
    assert "Don't auto-accept every answer" in md and "*c*" in md and "### Precision per answer" in md and "| c ⚠ |" in md
