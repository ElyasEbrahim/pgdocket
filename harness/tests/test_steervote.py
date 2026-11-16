"""`jevbench steervote` (#48): keep-one's votes weighted by length, without API calls."""
from jevbench import steervote as V


def test_a_long_piece_outweighs_several_short_ones():
    votes = [("network", 0.9, 30), ("network", 0.9, 30), ("storage", 0.8, 400)]
    assert V.weighted_outvoted("network", votes)                       # 0.8 × 400 > 2 × 0.9 × 30
    assert not V.weighted_outvoted("storage", votes)
    assert not V.weighted_outvoted("network", [("network", 0.5, 100), ("storage", 0.5, 100)])   # a tie leaves it


def test_route_flags_count_only_pieces_with_text_and_flag_one_piece():
    segs = [("title", "t" * 40), ("body", "b" * 400), ("body", "")]
    a = lambda tag: {"erase|0": ("x", 0.9), "erase|1": ("x", 0.9), "erase|2": ("x", 0.9),
                     "keep|0": ("x", 0.9), "keep|1": ("y", 0.6)}[tag]
    f = V.route_flags(segs, "x", a)
    assert not f["E1"] and not f["KO"] and f["KL"] and f["E1+KL"] and not f["E1+KO"]      # 0.6 × 400 > 0.9 × 40
    assert all(V.route_flags([("title", "only")], "x", a).values())


def test_yes_flags_weigh_a_no_by_its_probability_and_length():
    read = lambda a: (a[0], True, a[1])                              # (view answer, auto, P(yes))
    segs = [("title", "t" * 50), ("body", "b" * 600)]
    got = {("erase", 0): ("yes", 0.9), ("erase", 1): ("yes", 0.9), ("keep", 0): ("yes", 0.9), ("keep", 1): ("no", 0.2)}
    f = V.yes_flags(segs, got, read)
    assert f == {"E1": False, "E1+KV": False, "E1+KVL": True}        # KV: 0.9 > 0.8; KVL: 0.9 × 50 < 0.8 × 600


def test_the_rules_keep_the_current_check_when_others_flag_too_much():
    res = {k: {"flagged": 0.085, "calls": 10.6, "worst": 0.093} for k in V.ROUTE}
    res[("J120/N6", "E1+KL")] = {"flagged": 0.096, "calls": 10.6, "worst": 0.083}
    out, chosen = V.choose_route(res)
    assert ("J120/N6", "E1+KL") in out and chosen == ("J120/N6", "E1+KO")
    res[("J120/N6", "E1+KL")]["flagged"] = 0.09
    assert V.choose_route(res)[1] == ("J120/N6", "E1+KL")              # within the limit and 1 point better
    yes = {"E1": {"flagged": 0.079, "calls": 4.8, "worst_issues": 195}, "E1+KV": {"flagged": 0.224, "calls": 9.5, "worst_issues": 102},
           "E1+KVL": {"flagged": 0.10, "calls": 9.5, "worst_issues": 194}}
    assert V.choose_yes(yes) == (["E1+KV"], "E1")                         # within 2 issues: fewer calls
