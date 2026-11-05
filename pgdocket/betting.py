"""Intervals valid at any number of labels, by betting (#30).

PPI++'s interval, estimate ± z·se, rests on the central limit theorem: it holds once the labels contain enough rows
with and without the answer, which is why pgdocket waits for 10 of each (MIN_EACH_SIDE) before it trusts one. For a
rare answer that floor, not the target, sets the number of labels: an answer on 1.6 % of rows needs about 625.

Betting (Waudby-Smith & Ramdas, 2024) gives an interval with no floor. For each candidate share m, a gambler bets on
the labelled rows, one at a time, that m is wrong. If m were the true share, Ville's inequality says no strategy would
ever multiply its capital by 1/α except with probability α. So the shares at which the capital never reaches 1/α form
a confidence set, valid at any number of labels and wherever the labelling stops. As in prediction-powered
inference's nonasymptotic variant (Angelopoulos et al., 2023, App. C.2), the bets are on the rectifier x = y − λ·f.
pgdocket adapts it three ways:
- the model has answered every row in scope, so its share is known exactly and the whole α goes to the labels;
- the labels are drawn without replacement from a finite scope, so each bet is against the mean of the rows not yet
  labelled (Waudby-Smith & Ramdas, 2024, §5);
- λ, which PPI++ fits to all the labels, is fitted to the labels before each one, with half a disagreement added each
  way as elsewhere in pgdocket: a λ fixed before the label it applies to keeps the capital a martingale.
The bets are the predictable plug-in Waudby-Smith & Ramdas give for confidence sequences, b_t = √(2 log(2/α) /
(σ̂²_{t−1} t log(1 + t))), at most ¾ of the largest safe bet, made in both directions with half the capital each
(hedged). They don't depend on how many labels there will be, so a share once rejected stays rejected as rounds add
labels, and the interval holds whenever the rounds stop. Against bets tuned to a known number of labels they cost under
8 % of width where betting is used here (answers on 1–3 % of rows, up to 150 labels), and more for common answers,
which keep the normal interval. aGRAPA bets, which the audits use, gave intervals up to 63 % wider.

The price of validity at any n is width: where the normal interval holds, this one is wider (Ville's inequality
against the Gaussian tail, and bets that must work at every n), 1.7 times for an answer on 30 % of rows at 400 labels.
So pgdocket uses it only below the floor.
"""
import math

import numpy as np

GRID = 4001          # candidate shares when the scope has more rows than that (otherwise every k / N)
TRUNC = 0.75         # a bet stakes at most this share of the capital (WS&R use ½ or ¾; ¾ narrowed 1 % answers' intervals
                     # at 50–150 labels by a fifth, and left common answers' as they were)


def interval(y, f, model_total, rows, alpha=0.05, grid=GRID):
    """The betting interval for the share of rows whose true answer is y = 1, from labels drawn without replacement
    from `rows` rows, in the order they were drawn: y the labels (0/1), f the model's answers on those rows (0/1, or
    scores in [0, 1]), `model_total` the model's answers summed over all `rows`. Returns (low, high), or None when
    every share is rejected: then the true one was too, which happens with probability at most α (a miss)."""
    y, f = np.asarray(y, float), np.asarray(f, float)
    n, N = len(y), int(rows)
    if n >= N:                                           # every row labelled: the share is known
        t = float(y.sum()) / N
        return t, t
    exact = N + 1 <= grid
    m = np.arange(N + 1) / N if exact else np.linspace(0.0, 1.0, grid)   # candidate shares
    thr = math.log(2 / alpha)
    # all that doesn't depend on m, from the labels before each one: λ (the slope of y on f, with half a
    # disagreement each way: pseudo-rows (1, 0) and (0, 1)), the outcome z scaled to [0, 1], the bet size
    before = lambda x: np.concatenate([[0.0], np.cumsum(x)[:-1]])
    sy, sf, syf, sff = before(y), before(f), before(y * f), before(f * f)
    k = np.arange(n) + 1.0
    py, pf, pff = sy + 0.5, sf + 0.5, sff + 0.5
    var = pff - pf * pf / k
    with np.errstate(divide="ignore", invalid="ignore"):
        lam = np.where(var > 1e-12, np.clip((syf - py * pf / k) / var, 0.0, 1.0), 0.0)
    rest = N - np.arange(n)                              # rows not yet labelled, each label's own row among them
    muf = (model_total - sf) / rest                      # the model's share among them: known
    z = (y - lam * f + lam) / (1 + lam)                  # x = y − λ·f, scaled to [0, 1]
    zbar = (0.5 + np.cumsum(z)) / (np.arange(n) + 2)     # the running mean and spread of z (WS&R's regularised ones)
    ssd = 0.25 + np.concatenate([[0.0], np.cumsum((z - zbar) ** 2)[:-1]])
    b = np.sqrt(2 * thr / (np.log1p(k) * ssd))         # √(2 log(2/α) / (σ̂² t log(1 + t))): no n in it
    up, down = np.zeros(len(m)), np.zeros(len(m))        # log capital betting each way
    s0, block = 0, 16
    while s0 < n and len(m):                             # in blocks of labels, keeping only the shares not yet rejected
        sl = slice(s0, min(n, s0 + block))
        left = (N * m[None, :] - sy[sl, None]) / rest[sl, None]  # the share left with the answer, were the truth m
        possible = (left > -1e-9) & (left < 1 + 1e-9)    # N · (k / N) − k need not be exactly 0 in floating point
        muy = np.clip(left, 0.0, 1.0)
        c = (muy - (lam * muf)[sl, None] + lam[sl, None]) / (1 + lam[sl, None])
        with np.errstate(divide="ignore", invalid="ignore"):
            bu = np.minimum(b[sl, None], TRUNC / np.maximum(c, 1e-12))
            bd = np.minimum(b[sl, None], TRUNC / np.maximum(1 - c, 1e-12))
            gu = up + np.cumsum(np.log1p(bu * (z[sl, None] - c)), axis=0)
            gd = down + np.cumsum(np.log1p(-bd * (z[sl, None] - c)), axis=0)
            alive = (possible & (np.maximum(gu, gd) < thr)).all(axis=0)
        m, up, down = m[alive], gu[-1][alive], gd[-1][alive]
        s0, block = sl.stop, min(512, 2 * block)         # most shares fall early: small blocks first
    if not len(m):
        return None
    lo, hi = m.min(), m.max()
    if not exact:                                        # the true share may lie between two candidates
        step = 1.0 / (grid - 1)
        lo, hi = max(0.0, lo - step), min(1.0, hi + step)
    return float(lo), float(hi)


def typical(n, rows, model_total, q1, q0):
    """n labels as a random draw would most likely give them: the model's answers in proportion to its share of the
    rows, the true answer at rate q1 where the model said it and q0 where it didn't (rounded up: a rare answer seen
    once more than expected widens the interval), spread evenly through the order. Returns (y, f)."""
    n1 = min(n, int(round(n * model_total / rows)))
    n0 = n - n1
    k1, k0 = min(n1, math.ceil(n1 * q1 - 1e-9)), min(n0, math.ceil(n0 * q0 - 1e-9))
    cells = ((1, 1, k1), (0, 1, n1 - k1), (1, 0, k0), (0, 0, n0 - k0))
    pos = np.concatenate([(np.arange(c) + 0.5) / c for _, _, c in cells if c])
    y = np.concatenate([np.full(c, yy) for yy, _, c in cells if c])
    f = np.concatenate([np.full(c, ff) for _, ff, c in cells if c])
    order = np.argsort(pos, kind="stable")
    return y[order].astype(float), f[order].astype(float)


def labels_for(target, rows, model_total, q1, q0, alpha=0.05, start=1):
    """How many labels a betting interval needs for ± `target` (half its width), judged on a typical draw (`typical`):
    the smallest n from `start` on that gets there, or `rows` (every row labelled: the share is exact)."""
    def half(n):
        y, f = typical(n, rows, model_total, q1, q0)
        ci = interval(y, f, model_total, rows, alpha)
        return 1.0 if ci is None else (ci[1] - ci[0]) / 2

    start = max(1, int(start))
    if start >= rows or half(start) <= target:
        return min(start, rows)
    lo, hi = start, start
    while hi < rows:                                     # widths fall roughly as 1 / √n: step up by half each time
        lo, hi = hi, min(rows, math.ceil(hi * 1.5) + 1)
        if half(hi) <= target:
            break
    else:
        return rows
    while hi - lo > max(1, lo // 50):                    # then narrow it down to within 2 %
        mid = (lo + hi) // 2
        lo, hi = (lo, mid) if half(mid) <= target else (mid, hi)
    return hi
