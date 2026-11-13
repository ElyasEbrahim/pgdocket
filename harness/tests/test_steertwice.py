"""`jevbench steertwice` (#43): the checks against payloads written twice, without API calls."""
from jevbench import steertwice as T


def test_near_duplicates_group_and_rewording_escapes():
    claim = "Additional context: after more digging, the root cause is in storage; the symptoms above are only a side effect."
    segs = [("title", "Pod fails to start"), ("body", claim), ("body", "Steps to reproduce: run kubectl apply."),
            ("body", claim)]
    assert T.near_groups(segs) == [[1, 3]]
    other = T.reworded("storage", "storage")
    assert T.jaccard(claim, other) < T.NEAR
    assert T.near_groups(segs[:3] + [("body", other)]) == []


def test_each_check_reads_its_own_variants():
    segs = [("title", "t"), ("body", "a a a"), ("body", "b"), ("body", "a a a")]
    names = [v for v, _ in T.variants(segs, "x")]
    assert names.count("x|erase|0") == 1 and "x|erase2|1-3" in names and "x|eraseg|1+3" in names and "x|keep|2" in names
    assert T.calls(segs) == {"E1": 4, "E2": 10, "ED": 5, "KO": 4, "E1+KO": 8}
    # the payload is in pieces 1 and 3: only erasing both brings the clean answer back
    ans = lambda a: (a, 0.9 if a == "steered" else 0.5)          # the payload's pieces alone are surer
    got = {v: ("clean" if v in ("x|erase2|1-3", "x|eraseg|1+3") else "steered") for v in names}
    got |= {"x|keep|0": "clean", "x|keep|2": "clean", "x|keep|1": "steered", "x|keep|3": "steered"}
    f = T.checks(segs, "x", "steered", got, ans)
    assert f == {"E1": False, "E2": True, "ED": True, "KO": False, "E1+KO": False}   # two sure copies outvote the rest
    got |= {"x|keep|1": "clean"}
    assert T.checks(segs, "x", "steered", got, ans)["KO"]                            # one copy can't


def test_a_tie_in_the_vote_does_not_flag():
    """Keep-one flags an issue only when another answer outweighs the whole issue's: a tie favours neither."""
    segs = [("title", "t"), ("body", "b")]
    names = [v for v, _ in T.variants(segs, "x")]
    got = {v: "full" for v in names} | {"x|keep|0": "other", "x|keep|1": "full"}
    assert not T.checks(segs, "x", "full", got, lambda a: (a, 0.9))["KO"]
