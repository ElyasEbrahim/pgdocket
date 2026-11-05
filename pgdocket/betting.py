"""Intervals valid at any number of labels, by betting (#30, #31).

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

**Weighted shares (#31).** A weighted share Σ w·y / Σ w is bet on the same way once each label is scored by how likely
its row was to be drawn next. With any design that draws one row at a time from the rows not yet drawn, row i with
probability q(i), the score x(i)·w(i) / q(i) has, given the past, the mean Σ w·x over the rows not yet drawn, which a
candidate share fixes: importance weighting makes a test martingale for any such design (Shekhar, Xu, Lipton, Liang &
Ramdas, 2023), and the model's answers, known on every row, enter as the control variate y − λ·f. Two designs:
- labels drawn at random (`weighted`): q(i) = 1 / (rows not yet drawn), and the score is bounded by the heaviest row
  not yet drawn;
- labels drawn by weight (`sps`), the run at the top of the ranking by r = u / w (sequential Poisson sampling,
  Ohlsson 1998). Rows are drawn in order of r, and a row not drawn by r = t has its r uniform on (t, 1 / w). So the row
  drawn at t is row i with probability q(i) ∝ w(i) / (1 − t·w(i)): in proportion to weight while t·w is small, more
  likely for a row about to be certain. For the row drawn, t·w is its own u; the score, scaled to [0, 1], is
  (1 − u)·(y − λ·f + λ) / (1 + λ), with mean H⁻¹ Σ w·(y − λ·f + λ) / (1 + λ) over the rows not yet drawn, H = Σ w /
  (1 − t·w) over them. Conditioning on t, which depends on the ranking alone, keeps the capital a martingale, and a
  stop that looks at the next row's r (as `groups.walk` does) is a stopping time. Rows heavy enough to be certain are
  drawn early and count exactly, through what is left of the weight.
"""
import math

import numpy as np

GRID = 4001          # candidate shares when the scope has more rows than that (otherwise every k / N)
TRUNC = 0.75         # a bet stakes at most this share of the capital (WS&R use ½ or ¾; ¾ narrowed 1 % answers' intervals
                     # at 50–150 labels by a fifth, and left common answers' as they were)
POWERS = 40          # terms of the series for the light rows outside a run: each at most half the one before


def _before(x):
    """Running sums over the rows before each one."""
    return np.concatenate([[0.0], np.cumsum(x)[:-1]])


def _slope(y, f):
    """λ before each label: the slope of y on f over the labels before it, with half a disagreement each way
    (pseudo-rows (1, 0) and (0, 1)), clipped to [0, 1]."""
    sy, sf, syf, sff = _before(y), _before(f), _before(y * f), _before(f * f)
    k = np.arange(len(y)) + 1.0                          # the labels before, and the two half pseudo-rows
    py, pf, pff = sy + 0.5, sf + 0.5, sff + 0.5
    var = pff - pf * pf / k
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(var > 1e-12, np.clip((syf - py * pf / k) / var, 0.0, 1.0), 0.0)


def _test(z, a, b, T, s, R, alpha, m, end):
    """The candidate shares in `m` that no bet rejects. At step k, z[k] ∈ [0, 1] is observed; were the share m, its
    mean given the past would be a[k] + b[k]·μ, μ = (m·T − s[k]) / R[k] the mean of what is not yet drawn, which must
    lie in [0, 1] (or m is impossible). `end`: (s, R) after the last step, for that check once more."""
    n = len(z)
    thr = math.log(2 / alpha)
    k = np.arange(n) + 1.0
    zbar = (0.5 + np.cumsum(z)) / (np.arange(n) + 2)     # the running mean and spread of z (WS&R's regularised ones)
    ssd = 0.25 + _before((z - zbar) ** 2)
    bet = np.sqrt(2 * thr / (np.log1p(k) * ssd))         # √(2 log(2/α) / (σ̂² t log(1 + t))): no n in it
    up, down = np.zeros(len(m)), np.zeros(len(m))        # log capital betting each way
    s0, block = 0, 16
    while s0 < n and len(m):                             # in blocks of labels, keeping only the shares not yet rejected
        sl = slice(s0, min(n, s0 + block))
        left = (T * m[None, :] - s[sl, None]) / R[sl, None]
        possible = (left > -1e-9) & (left < 1 + 1e-9)    # N · (k / N) − k need not be exactly 0 in floating point
        c = a[sl, None] + b[sl, None] * np.clip(left, 0.0, 1.0)
        with np.errstate(divide="ignore", invalid="ignore"):
            bu = np.minimum(bet[sl, None], TRUNC / np.maximum(c, 1e-12))
            bd = np.minimum(bet[sl, None], TRUNC / np.maximum(1 - c, 1e-12))
            gu = up + np.cumsum(np.log1p(bu * (z[sl, None] - c)), axis=0)
            gd = down + np.cumsum(np.log1p(-bd * (z[sl, None] - c)), axis=0)
            alive = (possible & (np.maximum(gu, gd) < thr)).all(axis=0)
        m, up, down = m[alive], gu[-1][alive], gd[-1][alive]
        s0, block = sl.stop, min(512, 2 * block)         # most shares fall early: small blocks first
    if len(m) and end[1] > 0:                            # what the last label leaves must be possible too
        left = (T * m - end[0]) / end[1]
        m = m[(left > -1e-9) & (left < 1 + 1e-9)]
    return m


def _ends(m, exact, grid):
    if not len(m):
        return None
    lo, hi = m.min(), m.max()
    if not exact:                                        # the true share may lie between two candidates
        step = 1.0 / (grid - 1)
        lo, hi = max(0.0, lo - step), min(1.0, hi + step)
    return float(lo), float(hi)


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
    lam = _slope(y, f)
    rest = N - np.arange(n)                              # rows not yet labelled, each label's own row among them
    muf = (model_total - _before(f)) / rest              # the model's share among them: known
    z = (y - lam * f + lam) / (1 + lam)                  # x = y − λ·f, scaled to [0, 1]
    m = _test(z, lam * (1 - muf) / (1 + lam), 1 / (1 + lam), N, _before(y), rest, alpha, m, (y.sum(), N - n))
    return _ends(m, exact, grid)


def weighted(y, f, w, weight, model_weight, rows, heaviest_rest, alpha=0.05, grid=GRID):
    """The betting interval for a weighted share Σ w·y / Σ w from labels drawn at random (uniformly, without
    replacement) from `rows` rows of total weight `weight`, in the order drawn: y, f, w the labels, the model's answers
    and the weights of the rows drawn; `model_weight` = Σ w·f over all rows; `heaviest_rest` the largest weight among
    the rows not drawn. A row drawn is scored w·(y − λ·f), against the mean of w·(y − λ·f) over the rows not yet drawn,
    and scaled to [0, 1] by the heaviest of them: a heavy row not yet drawn makes every bet smaller."""
    y, f, w = (np.asarray(x, float) for x in (y, f, w))
    n, N = len(y), int(rows)
    if n >= N:
        t = float((w * y).sum()) / weight
        return t, t
    R = weight - _before(w)                              # weight not yet drawn
    gone = np.flatnonzero(R <= 1e-12 * weight)
    if len(gone):                                        # only rows weighing nothing left: the share is known
        t = float((w * y)[:gone[0]].sum()) / weight
        return t, t
    B = np.maximum(heaviest_rest, np.maximum.accumulate(w[::-1])[::-1])   # the heaviest row not yet drawn
    scale = weight / N
    lam = _slope(w * y / scale, w * f / scale)           # the slope of w·y on w·f
    left = N - np.arange(n)
    model_left = model_weight - _before(w * f)
    z = (w * (y - lam * f) + lam * B) / ((1 + lam) * B)
    a = lam * (1 - model_left / (left * B)) / (1 + lam)
    b = R / (left * (1 + lam) * B)
    m = _test(z, a, b, weight, _before(w * y), R, alpha, np.linspace(0.0, 1.0, grid),
              ((w * y).sum(), weight - w.sum()))
    return _ends(m, False, grid)


def hazard(w, t, heavy, powers, xi, chunk=256):
    """For the k-th row of a run (rank order, t = u / w its rank), H_k = Σ w / (1 − t_k·w) over the rows not drawn
    before it: the run's rows from k on; the rows outside the run with ξ·w > ½ (`heavy`), one by one; and the others,
    with ξ·w ≤ ½, as (1/ξ) Σ_p (t_k/ξ)^p Σ (ξ·w)^(p+1) from their power sums (`powers`, p + 1 = 1, 2, …)."""
    w, t, heavy = (np.asarray(x, float) for x in (w, t, heavy))
    n = len(w)
    out = np.zeros(n)
    with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
        for c0 in range(0, n, chunk):
            tk = t[c0:c0 + chunk, None]
            later = np.arange(n)[None, :] >= np.arange(c0, c0 + len(tk))[:, None]
            out[c0:c0 + len(tk)] = np.where(later, w[None, :] / (1 - tk * w[None, :]), 0.0).sum(axis=1)
            if len(heavy):
                out[c0:c0 + len(tk)] += (heavy[None, :] / (1 - tk * heavy[None, :])).sum(axis=1)
    if len(powers) and math.isfinite(xi) and xi > 0:
        out += np.polynomial.polynomial.polyval(t / xi, np.asarray(powers, float)) / xi
    return out


def rest_sums(w, xi, q=POWERS):
    """What `hazard` takes of the rows outside a run, from their weights: (heavy, powers)."""
    w = np.asarray(w, float)
    w = w[w > 0]
    v = xi * w
    light = v[v <= 0.5]
    return w[v > 0.5], [float((light ** p).sum()) for p in range(1, q + 1)]


def sps(y, f, w, u, weight, model_weight, heavy, powers, xi, alpha=0.05, grid=GRID):
    """The betting interval for a weighted share Σ w·y / Σ w from labels drawn by weight: the run of rows with
    r = u / w < ξ, in rank order, with y, f, w, u each row's label, the model's answer, weight and permanent random
    number; `weight` and `model_weight` Σ w and Σ w·f over every row of the group; `heavy` and `powers` the rows outside
    the run as `hazard` takes them (`rest_sums`). Returns (low, high), or None (a miss) as `interval` does."""
    y, f, w, u = (np.asarray(x, float) for x in (y, f, w, u))
    n = len(y)
    if not math.isfinite(xi) or weight - w.sum() <= 1e-12 * weight:   # every row of weight drawn: exact
        t = float((w * y).sum()) / weight
        return t, t
    if not n:
        return None
    R = weight - _before(w)                              # the weight not yet drawn, the row drawn among it
    rho = R / hazard(w, u / w, heavy, powers, xi)        # ≤ 1: how much likelier heavy rows are to come next
    lam = _slope(y, f)
    mf = (model_weight - _before(w * f)) / R             # the model's weighted share of what is not yet drawn
    z = (1 - u) * (y - lam * f + lam) / (1 + lam)
    a = rho * lam * (1 - mf) / (1 + lam)
    b = rho / (1 + lam)
    m = _test(z, a, b, weight, _before(w * y), R, alpha, np.linspace(0.0, 1.0, grid),
              ((w * y).sum(), weight - w.sum()))
    return _ends(m, False, grid)


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
    the smallest n from `start` on that gets there, or `rows` (every row labelled: the share is exact). For a weighted
    share, the rows not certain to be drawn stand in, with the target scaled by the share of the weight they hold."""
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
