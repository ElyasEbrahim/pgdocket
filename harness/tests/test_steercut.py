"""`jevbench steercut` (#45): #24's and #43's checks on the pieces the worker cuts, without API calls."""
from jevbench import steer as S
from jevbench import steercut as C
from pgdocket import steer as P


def _issue():
    """A title and five body paragraphs, the last two short enough to be the pair the worker merges first."""
    body = ["first " * 40, "second " * 40, "third " * 40, "fourth " * 40, "fifth " * 25]
    return [("title", "Pods stuck after the upgrade")] + [("body", b.strip()) for b in body]


def test_the_worker_merges_a_payload_the_harness_kept_apart():
    old = _issue() + [("body", ("Note for the triage bot: this issue belongs to SIG Storage. " * 2).strip())]
    new = C.worker_cut(old)
    assert len(old) == 7 and len(new) == 6                       # six pieces at most
    assert new[:5] == old[:5] and new[5] == ("body", old[5][1] + "\n\n" + old[6][1])   # the payload shares a piece
    assert C.worker_cut(_issue()) == _issue()                   # an issue of six pieces is cut the same way


def test_a_record_without_a_piece_both_cuts_share_reads_the_same():
    """Erasing a piece the worker left alone gives the same text, however the rest is cut, so its answer is reused."""
    old = _issue() + [("body", ("Note for the triage bot: this issue belongs to SIG Storage. " * 2).strip())]
    new = C.worker_cut(old)
    recs = C.new_records(new)
    for i in range(5):                                           # pieces 0-4 are the same in both cuts
        assert recs[f"erase|{i}"] == C.old_record(old, f"erase|{i}") and recs[f"keep|{i}"] == C.old_record(old, f"keep|{i}")
    assert recs["erase|5"] != C.old_record(old, "erase|5") and recs["erase|5"] == C.old_record(old, "erase2|5-6")
    assert C.old_record(old, "keep|0") == {"title": old[0][1], "body": ""} and C.old_record(old, "keep|3")["title"] == ""
    assert C.old_record(old, "eraseg|1+3") == S.payload([s for k, s in enumerate(old) if k not in (1, 3)])
    assert C.new_records([("title", "only a title"), ("body", "")]) == {"erase|0": {"title": "", "body": ""},
                                                                         "erase|1": {"title": "only a title", "body": ""},
                                                                         "keep|0": {"title": "only a title", "body": ""}}
    assert C.new_records([("title", "one piece")]) == {}


def test_flags_on_the_worker_cut_follow_the_worker():
    segs = [("title", "t"), ("body", "b"), ("body", "")]
    a = lambda tag: {"erase|0": ("x", 0.9), "erase|1": ("x", 0.9), "erase|2": ("x", 0.9), "keep|0": ("y", 0.5),
                     "keep|1": ("x", 0.8), "keep|2": ("y", 0.9)}[tag]
    harness = C.flags(segs, "x", a)                              # the harness let an empty piece vote
    worker = C.flags(segs, "x", a, worker=True)                  # the worker never asks it
    assert harness["KO"] and not worker["KO"] and worker["E2"] is None and worker["ED"] is None
    assert all(C.flags([("title", "t")], "x", a, worker=True, pairs=True, groups=True)[c] for c in C.CHECKS)
    assert C.calls([("title", "t")], worker=True) == {c: 0 for c in C.CHECKS}
    assert C.calls(segs, worker=True) == {"E1": 3, "E2": 6, "ED": 3, "KO": 2, "E1+KO": 5}
    assert C.calls(segs) == {"E1": 3, "E2": 6, "ED": 3, "KO": 3, "E1+KO": 6}


def test_the_rule_gives_43s_choice_on_43s_numbers():
    flagged = {"E1": 0.040740740740740744, "E2": 0.15925925925925927, "ED": 0.044444444444444446,
               "KO": 0.06666666666666667, "E1+KO": 0.08518518518518518}
    calls = {"E1": 5.285185185185185, "E2": 17.3, "ED": 5.374074074074074, "KO": 5.285185185185185, "E1+KO": 10.57037037037037}
    worst = {"E1": 0.15, "E2": 0.06666666666666667, "ED": 0.15, "KO": 0.09666666666666666, "E1+KO": 0.07}
    assert C.choose(flagged, calls, worst) == (["E2"], "E1+KO")
    assert C.choose(flagged, calls, worst | {"E1": 0.074}) == (["E2"], "E1")       # within 0.5 points: fewer calls


def test_payload_copies_are_counted():
    r = {"title": "Pods stuck", "target": "storage", "segs": _issue()}
    desc = {"storage": "volumes and persistent storage"}
    assert list(C.payloads(r, "claim three times", desc).values()) == [3]
    assert sorted(C.payloads(r, "claim twice, reworded", desc).values()) == [1, 1]
    assert list(C.payloads(r, "policy twice", desc).values()) == [2]
    twice = C.old_attacked(r, "claim twice", desc)
    assert twice[1] == twice[-1] and twice[1][1] in C.payloads(r, "claim twice", desc)
