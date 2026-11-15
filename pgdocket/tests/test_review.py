"""`calibrate --review` (#46): answers kept under review are never `auto`; nothing else changes."""
import numpy as np
import pytest

from pgdocket import calibration


def _noul(seed=42):
    rng = np.random.default_rng(seed)
    spec = {"kind": "noul", "question": "q"}
    y = rng.random(400) < 0.3
    p = np.round(np.clip(np.where(y, rng.beta(6, 2, 400), rng.beta(2, 6, 400)), 0.01, 0.99), 2)
    return spec, [{"noul": float(x)} for x in p], list(y)


def test_review_yes_keeps_only_the_no_band_automatic():
    spec, answers, y = _noul()
    cal = calibration.fit(spec, answers, y, precision_target=0.9, recall_target=0.9)
    reviewed = dict(cal, review=["yes"])
    assert any(calibration.auto(a, spec, cal) and calibration.view_answer(a, spec, cal) == "yes" for a in answers)
    for a in answers:
        ans = calibration.view_answer(a, spec, cal)
        assert calibration.view_answer(a, spec, reviewed) == ans                     # the answer doesn't change
        assert calibration.auto(a, spec, reviewed) == (ans == "no" and calibration.auto(a, spec, cal))
    assert calibration.still_automatic(spec, cal) == ["yes", "no"]
    assert calibration.still_automatic(spec, reviewed) == ["no"]
    assert calibration.still_automatic(spec, dict(cal, review=["no"])) == ["yes"]
    assert calibration.still_automatic(spec, dict(cal, review=["yes", "no"])) == []
    no_band = dict(calibration.fit(spec, answers, y, precision_target=0.9), review=["yes"])
    assert calibration.still_automatic(spec, no_band) == [] and not any(calibration.auto(a, spec, no_band) for a in answers)


def test_review_keeps_a_choice_answer_out_of_auto_overall_and_per_answer():
    rng = np.random.default_rng(7)
    opts = ["refund", "billing", "sales"]
    spec = {"kind": "choice", "question": "q", "options": [{"name": o} for o in opts]}
    pick = rng.choice(3, 300)
    answers = [{"choice": opts[k], "probabilities": {o: (0.96 if i == k else 0.02) for i, o in enumerate(opts)}}
               for k in pick]
    labels = [opts[k] for k in pick]                                                  # always right
    for per_answer in (False, True):
        cal = dict(calibration.fit(spec, answers, labels, precision_target=0.9, per_answer=per_answer), review=["refund"])
        got = [(opts[k], calibration.auto(a, spec, cal)) for k, a in zip(pick, answers)]
        assert {o for o, auto in got if auto} == {"billing", "sales"} and not any(auto for o, auto in got if o == "refund")
        assert calibration.still_automatic(spec, cal) == ["billing", "sales"]


def test_review_names_must_be_answers_of_the_decision():
    choice = {"kind": "choice", "question": "q", "options": [{"name": "refund"}, {"name": "sales"}]}
    assert calibration.review_list(choice, "refund, refund,") == ["refund"]
    assert calibration.review_list({"kind": "noul", "question": "q"}, ["yes"]) == ["yes"]
    assert calibration.review_list(choice, None) == [] and calibration.review_list(choice, "") == []
    with pytest.raises(ValueError, match="maybe is not an answer of this decision; it gives yes, no"):
        calibration.review_list({"kind": "noul", "question": "q"}, "maybe")
    with pytest.raises(ValueError, match="billing is not an answer of this decision; it gives refund, sales"):
        calibration.review_list(choice, "billing")
