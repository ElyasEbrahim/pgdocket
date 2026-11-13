"""`jevbench twicescores` (#39): five ways to read a row's three asks, and the rule that chose one."""
import numpy as np

from jevbench import twicescores as T


def test_every_reading_finds_the_odd_one_out_by_its_place():
    p0 = np.array([[0.9, 0.1, 0.0], [0.6, 0.4, 0.0], [0.5, 0.5, 0.0], [0.2, 0.7, 0.1]])
    p1 = np.array([[0.1, 0.9, 0.0], [0.6, 0.4, 0.0], [0.5, 0.5, 0.0], [0.2, 0.7, 0.1]])
    p2 = np.array([[0.1, 0.9, 0.0], [0.8, 0.2, 0.0], [0.5, 0.5, 0.0], [0.7, 0.2, 0.1]])
    o = T.odd_all(p0, p1, p2)
    # row 0: a new answer twice (stored odd); row 1: same answers, the second's score apart; row 2: all equal;
    # row 3: the second re-ask answers otherwise, with the same top score as the others
    assert o["A"].tolist() == [0, -1, -1, 2]
    assert o["B"].tolist() == [-1, 2, -1, -1]       # rows 0 and 3: the three top scores tie in pairs or all
    assert o["D"].tolist() == [0, 2, -1, 2]         # answers first, then scores
    assert o["E"].tolist() == [0, 2, -1, 2]


def test_readings_treat_the_three_asks_alike():
    """Swapping the asks swaps the odd one out: so without a change the stored one is odd a third of the time."""
    rng = np.random.default_rng(39)
    p = rng.dirichlet(np.ones(4), (3, 500))
    base = T.odd_all(*p)
    swapped = T.odd_all(p[1], p[0], p[2])
    for d in "ABDE":
        expect = np.select([base[d] == 0, base[d] == 1], [1, 0], base[d])
        assert (swapped[d] == expect).all()


def test_the_rule_drops_designs_out_and_minimises_the_worst_regret():
    cell = lambda **v: {d: {"at": v.get(d, 0.0), "end": v.get(d, 0.0)} for d in T.DESIGNS}
    cells = {"t · jev-1.14": cell(A=0, B=100, C=99, D=98, E=100), "t · drop": cell(A=100, B=2, C=99, D=60, E=70),
             "t · no change · from check 1": cell(A=50, B=50, C=50, D=50, E=50)}
    chosen, worst, mean, where = T.choose(cells, out_of=set())
    assert chosen == "C" and worst["C"] == 1 and worst["A"] == 100 and "no change" not in where["C"]
    chosen, worst, _, _ = T.choose(cells, out_of={"C"})
    assert chosen not in ("C",) and "C" not in worst


def test_a_change_with_several_asks_can_run_forward():
    """#40: a change asked more than once runs forward (the stored ask first, then two of the change's asks), even in a
    task whose other changes have their roles swapped; one whose new asks agree with each other is caught by its scores."""
    rng = np.random.default_rng(40)
    rows = 400
    base = rng.dirichlet(np.ones(4) * 0.5, rows)
    shifted = base.copy()
    shifted[:, 0], shifted[:, 1] = base[:, 1], base[:, 0]            # the change: two options swap probabilities
    task = {"orig": np.stack([base, base, base]), "truth": base.argmax(-1), "forward": False,
            "changes": {"swap": np.stack([shifted, shifted, shifted])}, "forward_changes": {"swap"}}
    cfg = {"labelled": 100, "anchors": 25, "checks": 6}
    first = T.series(task, cfg, 25, "swap", 1.0, 1, 10, rng)
    assert (first["C"] <= 6).all() and set(first) == set(T.DESIGNS)
    quiet = T.series(task, cfg, 25, None, 1.0, 1, 10, rng)
    assert all((f == T.NEVER).all() for f in quiet.values())   # every ask alike: nothing to find
