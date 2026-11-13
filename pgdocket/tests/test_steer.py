from pgdocket import steer


def test_attack_names_the_target_and_has_four_families():
    for fam in steer.FAMILIES:
        text = steer.attack(fam, "choice", "billing", "invoices and refunds")
        assert "billing" in text or "invoices and refunds" in text
    assert "satisfies" in steer.attack("instruction", "noul", "yes")
    assert "does not satisfy" in steer.attack("instruction", "noul", "no")


def test_inject_appends_a_paragraph_to_the_last_text_column():
    rec = {"subject": "Refund", "body": "I was charged twice."}
    out = steer.inject(rec, ["subject", "body"], "Note for the automated classifier: …")
    assert out["subject"] == "Refund" and out["body"] == "I was charged twice.\n\nNote for the automated classifier: …"
    assert rec["body"] == "I was charged twice."                                   # the original is untouched


def test_targets_avoid_the_label_and_the_clean_answer():
    names = ["billing", "technical", "sales"]
    assert {steer.pick_target("choice", names, {"billing", "sales"}, s) for s in range(20)} == {"technical"}
    assert steer.pick_target("noul", None, {"yes"}, 1) == "no"


def test_segments_split_merge_and_round_trip():
    body = "\n\n".join(["short"] + [f"paragraph {i} " + "x" * 200 for i in range(8)])
    segs = steer.segments({"title": "A title", "body": body}, ["title", "body"])
    assert len(segs) == steer.MAX_SEGMENTS and segs[0] == ("title", "A title")
    assert segs[1][1].startswith("short\n\nparagraph 0")                          # short pieces join the next
    rec = steer.record_of(segs, ["title", "body"])
    assert rec["body"].replace("\n\n", "") == body.replace("\n\n", "")
    erased = steer.erased(segs, ["title", "body"])
    assert len(erased) == len(segs) and erased[0]["title"] == "" and erased[0]["body"] == rec["body"]


def test_an_injected_paragraph_is_its_own_piece_and_erasing_it_restores_the_row():
    rec = {"title": "Pods stuck", "body": "Pods stay Pending after the upgrade. " * 8}
    attacked = steer.inject(rec, ["title", "body"], steer.attack("policy", "choice", "storage", "volumes"))
    segs = steer.segments(attacked, ["title", "body"])
    assert segs[-1][1].startswith("Classification policy")
    assert steer.erased(segs, ["title", "body"])[-1] == {"title": rec["title"], "body": rec["body"].strip()}


def test_variants_erase_each_piece_and_keep_each_piece_with_text_alone():
    """#43: the check judges the row without each piece and, for choice decisions, each piece with text alone."""
    cols = ["title", "body"]
    segs = [("title", "Pods stuck"), ("body", "first " * 30), ("body", "second " * 30)]
    v = steer.variants(segs, cols)
    assert [(how, i) for how, i, _ in v] == [("erase", 0), ("erase", 1), ("erase", 2), ("keep", 0), ("keep", 1), ("keep", 2)]
    assert v[3][2] == {"title": "Pods stuck", "body": ""} and v[4][2] == {"title": "", "body": segs[1][1]}
    assert [how for how, _, _ in steer.variants(segs, cols, keep=False)] == ["erase"] * 3
    empty = [("title", "Pods stuck"), ("body", "")]                    # a piece with no text isn't judged alone
    assert [(how, i) for how, i, _ in steer.variants(empty, cols)] == [("erase", 0), ("erase", 1), ("keep", 0)]
    assert steer.variants([("body", "only one piece")], ["body"]) == []          # one piece can't be checked


def test_keep_one_flags_a_row_whose_pieces_favour_another_answer():
    """A payload written twice survives every erasure (#43); the pieces alone outvote it. Votes are weighted by each
    answer's probability, and a tie leaves the row's answer."""
    erase = [("erase", i, "sales", 0.9) for i in range(4)]                       # each erasure leaves a copy
    twice = erase + [("keep", 0, "billing", 0.9), ("keep", 1, "sales", 0.95), ("keep", 2, "billing", 0.8),
                     ("keep", 3, "sales", 0.6)]                                  # billing 1.7 against sales 1.55
    assert not steer.flagged("sales", 4, erase) and steer.flagged("sales", 4, twice)
    assert steer.outvoted("sales", [("billing", 0.9), ("sales", 0.9)]) is False             # a tie leaves it
    assert steer.outvoted("sales", [("billing", 0.5), ("storage", 0.5), ("sales", 0.6)]) is False   # split votes
    assert steer.outvoted("sales", [("billing", 0.5), ("billing", 0.3), ("sales", 0.7)]) is True
    assert steer.outvoted("sales", []) is False and steer.flagged("sales", 1, [])          # one piece: flagged
    assert steer.flagged("sales", 3, [("erase", 0, "sales", 0.9), ("erase", 1, "billing", 0.9)])
