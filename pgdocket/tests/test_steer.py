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
