import math

import numpy as np
import pytest

from pgdocket import groups
from pgdocket.estimate import norm_ppf, ppi_share

Z = norm_ppf(0.975)


def _counts(f, y, lab):
    f, y, lab = (np.asarray(x) for x in (f, y, lab))
    return {"n": int(lab.sum()), "k": int(y[lab].sum()), "sf_l": int(f[lab].sum()), "syf": int((f * y)[lab].sum()),
            "nu": int((~lab).sum()), "sf_u": int(f[~lab].sum())}


def _group(g, f, y, lab):
    c = _counts(f, y, lab)
    return {"g": g, "judged": c["n"] + c["nu"], "n": c["n"], "k": c["k"], "sf_l": c["sf_l"], "syf": c["syf"],
            "sf_u": c["sf_u"]}


def _sample(rng, N, share, agree, n_labels, flip_to_one=0.0):
    """N rows with the answer at `share`; the model agrees with the truth on `agree` of them, and also says yes on
    `flip_to_one` of the true noes (a systematic over-count)."""
    y = (rng.random(N) < share).astype(int)
    f = np.where(rng.random(N) < agree, y, 1 - y)
    f = np.where((y == 0) & (rng.random(N) < flip_to_one), 1, f)
    lab = np.zeros(N, bool)
    lab[rng.choice(N, n_labels, replace=False)] = True
    return f, y, lab


def test_ppi_binary_is_estimate_ppi_share_from_counts():
    rng = np.random.default_rng(1)
    for _ in range(25):
        N = int(rng.integers(60, 600))
        f, y, lab = _sample(rng, N, rng.uniform(0.1, 0.9), 0.8, int(rng.integers(20, N // 2)))
        c = _counts(f, y, lab)
        got = groups.ppi_binary(c["n"], c["k"], c["sf_l"], c["syf"], c["nu"], c["sf_u"])
        ref = ppi_share(f[~lab], f[lab], y[lab])
        if ref["estimate"] is None:
            assert got is None
            continue
        assert got["theta"] == pytest.approx(ref["estimate"], rel=1e-12)
        assert got["se"] == pytest.approx(ref["se"], rel=1e-9)
        assert got["lam"] == pytest.approx(ref["lam"], rel=1e-12, abs=1e-15)


def test_every_row_labelled_is_exact_and_too_few_labels_is_no_estimate():
    assert groups.ppi_binary(3, 1, 1, 1, 0, 0) == {"theta": 1 / 3, "se": 0.0, "lam": 0.0, "res_var": 0.0, "exact": True}
    assert groups.ppi_binary(40, 0, 3, 0, 100, 9) is None          # no labelled row with the answer
    assert groups.ppi_binary(1, 1, 1, 1, 100, 9) is None


def test_plan_pilots_unknown_groups_and_sizes_the_rest_from_their_own_disagreement():
    rng = np.random.default_rng(2)
    big = _group("big", *_sample(rng, 5000, 0.3, 0.9, 400))
    noisy = _group("noisy", *_sample(rng, 800, 0.3, 0.6, 100))
    new = _group("new", *_sample(rng, 300, 0.3, 0.9, 5))
    tiny = _group("tiny", *_sample(rng, 12, 0.3, 0.9, 2))
    gs = groups.summarise([big, noisy, new, tiny], Z)
    p = {r["group"]: r for r in groups.plan(gs, 0.05, Z)["groups"]}
    assert p["new"]["kind"] == "pilot" and p["new"]["need"] == groups.PILOT
    assert p["tiny"]["kind"] == "exact" and p["tiny"]["need"] == 12            # label all 12: the share is then exact
    for name in ("big", "noisy"):
        g = next(x for x in gs if x["g"] == name)
        width = math.ceil(Z * Z * g["est"]["res_var"] / 0.05 ** 2)
        assert p[name]["need"] == max(g["n"], width, math.ceil(10 / min(g["est"]["theta"], 1 - g["est"]["theta"])))
    assert p["big"]["more"] == 0 and p["noisy"]["more"] > 0   # labels go where the model disagrees with people
    assert groups.plan(gs, 0.05, Z)["uniform"] is None                        # can't compare before the pilots


def test_plan_compares_with_labels_drawn_across_the_table():
    rng = np.random.default_rng(3)
    gs = groups.summarise([_group("a", *_sample(rng, 9000, 0.3, 0.9, 300)),
                           _group("b", *_sample(rng, 1000, 0.3, 0.9, 100))], Z)
    p = groups.plan(gs, 0.03, Z)
    need = {r["group"]: r["need"] for r in p["groups"]}
    assert p["uniform"] == min(10000, max(math.ceil(need["a"] * 10000 / 9000), math.ceil(need["b"] * 10000 / 1000)))
    assert p["uniform"] > p["need"]


def test_a_group_the_model_gets_systematically_wrong_is_flagged():
    rng = np.random.default_rng(4)
    fair = [_group(f"g{i}", *_sample(rng, 2000, 0.2, 0.92, 300)) for i in range(4)]
    skewed = _group("skewed", *_sample(rng, 2000, 0.2, 0.92, 300, flip_to_one=0.25))   # over-counts by ~20 points
    flags = groups.systematic(groups.summarise(fair + [skewed], Z))
    assert [f["group"] for f in flags] == ["skewed"] and flags[0]["bias"] > 0.1
    assert groups.systematic(groups.summarise(fair, Z)) == []


def test_all_groups_is_the_size_weighted_estimate():
    rng = np.random.default_rng(5)
    gs = groups.summarise([_group("a", *_sample(rng, 3000, 0.3, 0.9, 200)),
                           _group("b", *_sample(rng, 1000, 0.6, 0.9, 200))], Z)
    o = groups.combine(gs, Z)
    assert o["theta"] == pytest.approx(0.75 * gs[0]["est"]["theta"] + 0.25 * gs[1]["est"]["theta"])
    assert o["se"] == pytest.approx(math.hypot(0.75 * gs[0]["est"]["se"], 0.25 * gs[1]["est"]["se"]))
    assert groups.combine(gs + groups.summarise([{"g": "c", "judged": 50, "n": 0, "k": 0, "sf_l": 0, "syf": 0,
                                                  "sf_u": 10}], Z), Z) is None


def test_render_names_the_flagged_group_and_the_saving():
    rng = np.random.default_rng(6)
    gs = groups.summarise([_group(f"g{i}", *_sample(rng, 2000, 0.2, 0.92, 300)) for i in range(3)]
                          + [_group("skewed", *_sample(rng, 2000, 0.2, 0.92, 300, flip_to_one=0.25))], Z)
    r = {"name": "t", "by": "region", "answer": "billing", "level": 0.95, "where": None, "groups": gs,
         "overall": groups.combine(gs, Z), "systematic": groups.systematic(gs), "plan": groups.plan(gs, 0.02, Z)}
    out = groups.render(r)
    assert "by region" in out and "overstates 'billing'" in out and "in skewed" in out
    assert "drawn at random across all groups instead, about" in out and "all groups" in out
