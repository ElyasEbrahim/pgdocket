"""`jevbench steerclaims` (#50): the sentence that doubts the author's conclusions, without API calls."""
from jevbench import steerclaims as L


def _rows(n):
    return [{"key": str(i), "label": "a", "target": "b"} for i in range(n)]


def test_clean_numbers_count_right_auto_flagged_and_left():
    rows = _rows(4)
    answers = {"0": ("a", 0.9), "1": ("a", 0.5), "2": ("b", 0.95), "3": ("a", 0.99)}
    got = L.clean_numbers(rows, answers, 0.8, {"0": False, "2": True, "3": False})
    assert got == {"right": 3, "auto": 3, "flagged": 1, "left": 2, "left_right": 2}


def test_the_rule_is_the_one_fixed_in_the_issue():
    cur = {"right": 271, "left": 247}
    assert L.rule(cur, {"right": 268, "left": 238}, 59, 29) == {"accuracy": True, "auto_left": True, "o2_within_10": True}
    assert L.rule(cur, {"right": 267, "left": 238}, 59, 29)["accuracy"] is False        # 1.3 points lower
    assert L.rule(cur, {"right": 271, "left": 237}, 59, 29)["auto_left"] is False       # 3.3 points lower
    assert L.rule(cur, {"right": 271, "left": 247}, 59, 30)["o2_within_10"] is False    # 30 is more than half of 59


def test_the_sentence_follows_the_current_one():
    assert L.CLAIMS.startswith(" ") and "made up" in L.CLAIMS and L.CLAIMS.endswith(".")
