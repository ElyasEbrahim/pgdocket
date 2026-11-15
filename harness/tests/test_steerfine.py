"""`jevbench steerfine` (#47): a finer cut for --harden's check, without API calls."""
from jevbench import steerfine as F
from pgdocket import steer as P


def test_the_current_cut_is_the_workers():
    body = "\n\n".join(["### What happened?", "first " * 30, "short tail", "second " * 30, "</details>"] + ["more " * 30] * 6)
    rec = {"title": "Pods stuck", "body": body}
    assert F.cut(rec, F.CURRENT) == P.segments(rec, F.COLS)
    assert len(F.cut(rec, "J120/N8")) == 8 and len(F.cut(rec, "J120/N12")) > 8
    headed = F.cut({"title": "t", "body": "### What happened?\n\n" + "x " * 40 + "\n\n" + "y " * 30 + "\n\nshort tail, sixty characters long or so, give or take a few"}, "J40/N10")
    assert headed[1][1].startswith("### What happened?\n\nx")                    # a heading still joins its section
    assert headed[-1][1].startswith("short tail")                                # a short closing line stays apart


def test_the_rule_drops_cuts_over_the_limits_and_prefers_fewer_calls_within_half_a_point():
    res = {"J120/N6": {"flagged": 0.085, "calls": 10.6, "worst": 0.093},
           "J120/N8": {"flagged": 0.088, "calls": 12.5, "worst": 0.060},
           "J120/N12": {"flagged": 0.095, "calls": 13.5, "worst": 0.040},              # too many clean flags
           "J40/N10": {"flagged": 0.080, "calls": 16.0, "worst": 0.030}}               # more than 1.5 × the calls
    assert F.choose(res) == (["J120/N12", "J40/N10"], "J120/N8")
    res["J120/N8"]["worst"] = 0.097
    assert F.choose(res) == (["J120/N12", "J40/N10"], "J120/N6")                       # within 0.5 points: fewer calls


def test_a_new_cut_applies_to_choice_decisions_only_if_it_hurts_the_no_side():
    cur = {"worst": 0.033, "flagged": 0.021}
    assert F.scope(cur, {"worst": 0.038, "flagged": 0.07}) == "every decision"
    assert F.scope(cur, {"worst": 0.039, "flagged": 0.02}) == "choice decisions only"
    assert F.scope(cur, {"worst": 0.0, "flagged": 0.072}) == "choice decisions only"


def test_the_yes_side_is_sampled_only_when_the_cap_would_be_passed():
    assert F.share_that_fits(0.05, 0.10, 0.20) == 1.0
    assert F.share_that_fits(0.05, 0.20, 0.20) == 0.75 and F.share_that_fits(0.25, 0.10, 0.20) == 0.0
