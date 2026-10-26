"""Calibration and classification metrics, recalibrators and bootstrap CIs (numpy only)."""
import math

import numpy as np

EPS = 1e-12


# ---------------------------------------------------------------- calibration error
def reliability_bins(conf, correct, n_bins=10, scheme="width"):
    """Rows of (lo, hi, count, mean_confidence, accuracy). scheme: 'width' (equal-width) or 'mass' (equal-count)."""
    conf, correct = np.asarray(conf, float), np.asarray(correct, float)
    if scheme == "width":
        edges = np.linspace(0.0, 1.0, n_bins + 1)
    else:
        edges = np.unique(np.quantile(conf, np.linspace(0, 1, n_bins + 1)))
        edges[0], edges[-1] = 0.0, 1.0
    idx = np.clip(np.searchsorted(edges, conf, side="right") - 1, 0, len(edges) - 2)
    rows = []
    for b in range(len(edges) - 1):
        m = idx == b
        if m.any():
            rows.append((edges[b], edges[b + 1], int(m.sum()), float(conf[m].mean()), float(correct[m].mean())))
        else:
            rows.append((edges[b], edges[b + 1], 0, float("nan"), float("nan")))
    return rows


def ece(conf, correct, n_bins=10, scheme="width"):
    """Expected calibration error: count-weighted |accuracy - confidence| over bins."""
    rows = reliability_bins(conf, correct, n_bins, scheme)
    n = sum(r[2] for r in rows)
    return float(sum(r[2] * abs(r[4] - r[3]) for r in rows if r[2]) / max(n, 1))


def brier_binary(p, y):
    p, y = np.asarray(p, float), np.asarray(y, float)
    return float(np.mean((p - y) ** 2))


def brier_multi(P, y_idx):
    P = np.asarray(P, float)
    Y = np.zeros_like(P)
    Y[np.arange(len(y_idx)), y_idx] = 1.0
    return float(np.mean(np.sum((P - Y) ** 2, axis=1)))


def nll_multi(P, y_idx):
    P = np.asarray(P, float)
    return float(-np.mean(np.log(np.clip(P[np.arange(len(y_idx)), y_idx], EPS, 1.0))))


def nll_binary(p, y):
    p, y = np.clip(np.asarray(p, float), EPS, 1 - EPS), np.asarray(y, float)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


# ---------------------------------------------------------------- classification
def auroc(scores, y):
    """Mann–Whitney AUROC with average ranks for ties."""
    s, y = np.asarray(scores, float), np.asarray(y, bool)
    n_pos, n_neg = int(y.sum()), int((~y).sum())
    if n_pos == 0 or n_neg == 0:
        return float("nan")
    order = np.argsort(s, kind="mergesort")
    ranks = np.empty(len(s))
    sorted_s = s[order]
    i = 0
    while i < len(s):
        j = i
        while j + 1 < len(s) and sorted_s[j + 1] == sorted_s[i]:
            j += 1
        ranks[order[i:j + 1]] = (i + j) / 2.0 + 1.0
        i = j + 1
    return float((ranks[y].sum() - n_pos * (n_pos + 1) / 2.0) / (n_pos * n_neg))


def prf(pred, y):
    pred, y = np.asarray(pred, bool), np.asarray(y, bool)
    tp, fp, fn = int((pred & y).sum()), int((pred & ~y).sum()), int((~pred & y).sum())
    p = tp / (tp + fp) if tp + fp else float("nan")
    r = tp / (tp + fn) if tp + fn else float("nan")
    f = 2 * p * r / (p + r) if tp else 0.0
    return p, r, f


def macro_f1(pred_idx, y_idx, k):
    pred_idx, y_idx = np.asarray(pred_idx), np.asarray(y_idx)
    f1s = [prf(pred_idx == c, y_idx == c)[2] for c in range(k) if (y_idx == c).any()]
    return float(np.mean(f1s)) if f1s else float("nan")


def best_f1_threshold(p, y):
    p, y = np.asarray(p, float), np.asarray(y, bool)
    best = (0.0, 0.5)
    for t in np.unique(np.round(p, 4)):
        f = prf(p >= t, y)[2]
        if f > best[0]:
            best = (f, float(t))
    return best


# ---------------------------------------------------------------- recalibration
def isotonic_fit(x, y, w=None):
    """Pool-adjacent-violators. Returns (block_x, block_y) knots for monotone non-decreasing interpolation."""
    x, y = np.asarray(x, float), np.asarray(y, float)
    w = np.ones_like(y) if w is None else np.asarray(w, float)
    order = np.argsort(x, kind="mergesort")
    xs, ys, ws = x[order], y[order], w[order]
    vals, wts, xsum, cnt = [], [], [], []
    for xi, yi, wi in zip(xs, ys, ws):
        vals.append(yi); wts.append(wi); xsum.append(xi * wi); cnt.append(wi)
        while len(vals) > 1 and vals[-2] > vals[-1]:
            v2, w2, x2, c2 = vals.pop(), wts.pop(), xsum.pop(), cnt.pop()
            v1, w1 = vals[-1], wts[-1]
            vals[-1] = (v1 * w1 + v2 * w2) / (w1 + w2)
            wts[-1] = w1 + w2
            xsum[-1] += x2
            cnt[-1] += c2
    bx = np.array([s / c for s, c in zip(xsum, cnt)])
    by = np.array(vals)
    return bx, by


def isotonic_predict(knots, x):
    bx, by = knots
    if len(bx) == 1:
        return np.full(len(np.atleast_1d(x)), by[0])
    return np.interp(np.asarray(x, float), bx, by)


def platt_fit(p, y, l2=1e-3, iters=50):
    """Logistic regression of y on logit(p): returns (a, b) with p' = sigmoid(a * logit(p) + b)."""
    p = np.clip(np.asarray(p, float), 1e-6, 1 - 1e-6)
    x = np.log(p / (1 - p))
    y = np.asarray(y, float)

    def loss(a, b):
        z = a * x + b
        return float(np.sum(np.logaddexp(0.0, z) - y * z) + 0.5 * l2 * a * a)

    a, b = 1.0, 0.0
    cur = loss(a, b)
    for _ in range(iters):
        q = 0.5 * (1.0 + np.tanh(0.5 * (a * x + b)))       # numerically stable sigmoid
        g = np.array([np.sum((q - y) * x) + l2 * a, np.sum(q - y)])
        wq = q * (1 - q)
        H = np.array([[np.sum(wq * x * x) + l2, np.sum(wq * x)], [np.sum(wq * x), np.sum(wq) + 1e-9]])
        step = np.linalg.lstsq(H, g, rcond=None)[0]
        t = 1.0
        while t > 1e-6:                                     # damped Newton: never accept a step that raises the loss
            na, nb = a - t * step[0], b - t * step[1]
            new = loss(na, nb)
            if new <= cur:
                break
            t *= 0.5
        else:
            break
        a, b, improvement, cur = na, nb, cur - new, new
        if improvement < 1e-10 * max(1.0, abs(cur)):
            break
    return a, b


def platt_predict(params, p):
    a, b = params
    p = np.clip(np.asarray(p, float), 1e-6, 1 - 1e-6)
    return 1.0 / (1.0 + np.exp(-(a * np.log(p / (1 - p)) + b)))


def temperature_fit(P, y_idx, lo=0.05, hi=20.0, iters=80):
    """Single temperature T minimising NLL of softmax(log P / T); golden-section search on log T."""
    logP = np.log(np.clip(np.asarray(P, float), EPS, 1.0))

    def nll(logT):
        Z = logP / np.exp(logT)
        Z = Z - Z.max(axis=1, keepdims=True)
        Q = np.exp(Z)
        Q /= Q.sum(axis=1, keepdims=True)
        return nll_multi(Q, y_idx)

    a, b = np.log(lo), np.log(hi)
    g = (np.sqrt(5) - 1) / 2
    c, d = b - g * (b - a), a + g * (b - a)
    for _ in range(iters):
        if nll(c) < nll(d):
            b = d
        else:
            a = c
        c, d = b - g * (b - a), a + g * (b - a)
    return float(np.exp((a + b) / 2))


def temperature_apply(P, T):
    Z = np.log(np.clip(np.asarray(P, float), EPS, 1.0)) / T
    Z = Z - Z.max(axis=1, keepdims=True)
    Q = np.exp(Z)
    return Q / Q.sum(axis=1, keepdims=True)


def kfold_indices(n, k=5, seed=0):
    idx = np.random.default_rng(seed).permutation(n)
    return [idx[i::k] for i in range(k)]


def crossfit_binary(p, y, method, k=5, seed=0):
    """Out-of-fold recalibrated probabilities, so the 'after' numbers are not fitted on the rows they score."""
    p, y = np.asarray(p, float), np.asarray(y, float)
    out = np.empty_like(p)
    for test in kfold_indices(len(p), k, seed):
        train = np.setdiff1d(np.arange(len(p)), test)
        if method == "isotonic":
            out[test] = isotonic_predict(isotonic_fit(p[train], y[train]), p[test])
        elif method == "platt":
            out[test] = platt_predict(platt_fit(p[train], y[train]), p[test])
        else:
            raise ValueError(method)
    return out


def crossfit_temperature(P, y_idx, k=5, seed=0):
    P, y_idx = np.asarray(P, float), np.asarray(y_idx)
    out, temps = np.empty_like(P), []
    for test in kfold_indices(len(P), k, seed):
        train = np.setdiff1d(np.arange(len(P)), test)
        T = temperature_fit(P[train], y_idx[train])
        temps.append(T)
        out[test] = temperature_apply(P[test], T)
    return out, float(np.median(temps))



# ---------------------------------------------------------------- two-sample tests (drift, #22)
def proportion_tests(counts_a, counts_b, min_expected=5):
    """Has any category's share changed between two samples? A two-sided two-proportion z-test per category, for the
    categories expected at least `min_expected` times on each side. Returns ([(category, share_a, share_b, p)], k):
    k is the number of distinct tests, for a Bonferroni correction (with two categories both rows are one test)."""
    na, nb = sum(counts_a.values()), sum(counts_b.values())
    cats = sorted(set(counts_a) | set(counts_b))
    out = []
    for c in cats:
        a, b = counts_a.get(c, 0), counts_b.get(c, 0)
        pool = (a + b) / (na + nb)
        if min(na, nb) * min(pool, 1 - pool) < min_expected:
            continue
        z = (b / nb - a / na) / math.sqrt(pool * (1 - pool) * (1 / na + 1 / nb))
        out.append((c, a / na, b / nb, math.erfc(abs(z) / math.sqrt(2))))
    return out, (1 if len(cats) == 2 else len(out))


def ks_2samp(a, b):
    """Two-sample Kolmogorov-Smirnov test: (D, asymptotic p). Ties make it conservative."""
    a, b = np.sort(np.asarray(a, float)), np.sort(np.asarray(b, float))
    both = np.concatenate([a, b])
    d = float(np.max(np.abs(np.searchsorted(a, both, side="right") / len(a) - np.searchsorted(b, both, side="right") / len(b))))
    en = math.sqrt(len(a) * len(b) / (len(a) + len(b)))
    lam = (en + 0.12 + 0.11 / en) * d
    if lam < 0.2:
        return d, 1.0
    p = sum(2 * (-1) ** (j - 1) * math.exp(-2 * j * j * lam * lam) for j in range(1, 101))
    return d, min(max(p, 0.0), 1.0)


def spent_alpha(alpha, run):
    """Error budget for the run-th of an open-ended series of checks: alpha * 6 / (pi^2 run^2). The budgets sum to
    alpha, so the chance of any false alarm over every run stays at most alpha (a union bound)."""
    return alpha * 6 / (math.pi ** 2 * run ** 2)


# ---------------------------------------------------------------- anytime-valid tests by betting (audit, #23)
def eprocess_below(x, target, cap=0.5, window=40, stop=None):
    """Evidence that the mean of x (values in [0, 1], in the order observed) has fallen below `target`.

    Wealth W_t = prod (1 + lambda_i (target - x_i)). While each x_i has conditional mean >= target, W is a
    nonnegative supermartingale for any lambda_i in [0, 1 / (1 - target)] chosen from x_1..x_{i-1}, so by Ville's
    inequality P(W ever reaches 1 / alpha) <= alpha: the test may be read after every observation, for ever. The
    bets follow aGRAPA (Waudby-Smith & Ramdas, 2024), capped at `cap` / (1 - target) so that one observation can at
    most halve the wealth. They are sized from the last `window` observations plus one pseudo-observation at the
    target (no bet without evidence): a mean over all of them would keep a long good stretch from betting after a
    change. Returns the wealth after each observation (up to the first that reaches `stop`, if given)."""
    x = np.asarray(x, float)
    w, out = 1.0, []
    for i, xi in enumerate(x):
        recent = x[max(0, i - window):i]
        n = len(recent) + 1
        mu = (target + recent.sum()) / n
        var = (target * (1 - target) + ((recent - mu) ** 2).sum()) / n
        gap = target - mu
        lam = min(gap / (var + gap * gap), cap / (1 - target)) if gap > 0 else 0.0
        w *= 1 + lam * (target - xi)
        out.append(w)
        if stop is not None and w >= stop:
            break
    return np.array(out)



def recall_threshold(score, positive, target=0.9, alpha=0.05):
    """Highest threshold on `score` that keeps recall >= target with probability >= 1 - alpha (#21).

    Rows scored below it can be answered "no" without review: at most (1 - target) of the true positives are among
    them. Candidates are the distinct scores, which don't depend on the labels. They are tested from the lowest
    (every positive kept) upward and testing stops at the first failure: recall can only fall as the threshold
    rises, so this fixed-sequence procedure needs no correction. Each test is a one-sided Clopper-Pearson bound on
    recall over the labelled positives, a random sample of all positives when the labels were drawn at random. Even
    with every positive kept, n positives can only prove targets up to alpha^(1/n): `positives_needed` says how many.
    """
    score, positive = np.asarray(score, float), np.asarray(positive, bool)
    n = int(positive.sum())
    need = math.ceil(math.log(alpha) / math.log(target)) if 0 < target < 1 else 1
    out = {"target": target, "alpha": alpha, "positives": n, "positives_needed": need, "chosen": None}
    pos = score[positive]
    for t in np.unique(score):
        k = int((pos >= t).sum())
        if not n or clopper_pearson_lower(k, n, alpha) < target:
            break
        out["chosen"] = {"threshold": float(t), "recall": k / n, "lower_bound": clopper_pearson_lower(k, n, alpha),
                         "dismissed": float((score < t).mean())}
    return out

def per_answer(score, correct, answer, threshold, target=0.9, alpha=0.05):
    """Precision per answer among the rows `auto` would accept (#19).

    `threshold` is one number (the overall guarantee), a dict answer -> threshold (one guarantee per answer) or None.
    For each answer: how many rows were accepted, how many of those were right, one-sided Clopper-Pearson bounds at
    1 - alpha, `below_target` when even the upper bound is under the target (demonstrably worse, not just few rows),
    and the threshold the answer would get under its own guarantee (None when its rows can't certify the target).
    """
    score, correct, answer = np.asarray(score, float), np.asarray(correct, float), np.asarray(answer)
    out = []
    for a in sorted(set(answer.tolist()), key=lambda a: (-(answer == a).sum(), a)):
        m = answer == a
        t = threshold.get(a) if isinstance(threshold, dict) else threshold
        acc = m & (score >= t) if t is not None else np.zeros(len(m), bool)
        n, k = int(acc.sum()), int(correct[acc].sum())
        upper = 1 - clopper_pearson_lower(n - k, n, alpha) if n else None
        own = precision_threshold(score[m], correct[m], target, alpha)["chosen"]
        out.append({"answer": a, "answered": int(m.sum()), "auto": n, "right": k,
                    "precision": k / n if n else None, "lower": clopper_pearson_lower(k, n, alpha) if n else None,
                    "upper": upper, "below_target": bool(n and upper < target),
                    "own_threshold": own["threshold"] if own else None, "own_auto": own["accepted"] if own else 0})
    return out

# ---------------------------------------------------------------- uncertainty
def bootstrap_ci(stat, arrays, n_boot=1000, seed=0, alpha=0.05):
    """Percentile bootstrap CI for stat(*arrays) resampling rows jointly."""
    arrays = [np.asarray(a) for a in arrays]
    n = len(arrays[0])
    rng = np.random.default_rng(seed)
    vals = []
    for _ in range(n_boot):
        i = rng.integers(0, n, n)
        vals.append(stat(*[a[i] for a in arrays]))
    vals = np.asarray(vals, float)
    vals = vals[~np.isnan(vals)]
    if not len(vals):
        return float("nan"), float("nan")
    return float(np.quantile(vals, alpha / 2)), float(np.quantile(vals, 1 - alpha / 2))


# ---------------------------------------------------------------- guarantees
def _log_binom_tail(k, n, p):
    """log P(X >= k) for X ~ Binomial(n, p)."""
    import math
    if k <= 0:
        return 0.0
    if p <= 0.0:
        return float("-inf")
    if p >= 1.0:
        return 0.0
    j = np.arange(k, n + 1)
    lg = np.array([math.lgamma(n + 1) - math.lgamma(x + 1) - math.lgamma(n - x + 1) for x in j])
    terms = lg + j * math.log(p) + (n - j) * math.log1p(-p)
    m = terms.max()
    return float(m + np.log(np.exp(terms - m).sum()))


def clopper_pearson_lower(k, n, alpha=0.05):
    """One-sided (1 - alpha) Clopper–Pearson lower bound on a proportion, from k successes in n trials."""
    if n == 0 or k == 0:
        return 0.0
    lo, hi = 0.0, k / n
    target = np.log(alpha)
    for _ in range(80):                          # P(X >= k | p) increases with p
        mid = (lo + hi) / 2
        if _log_binom_tail(k, n, mid) < target:
            lo = mid
        else:
            hi = mid
    return lo


def precision_threshold(score, correct, target=0.9, alpha=0.05, grid=20, min_accept=20):
    """Lowest threshold on `score` whose accepted rows have precision >= target with probability >= 1 - alpha.

    Candidate thresholds are score quantiles (accepting 5 %, 10 %, ... of rows), so they don't depend on the
    labels. They are tested in a fixed order, from fewest to most rows accepted, stopping at the first failure.
    That controls the chance of any false claim at alpha without a multiple-testing correction (fixed-sequence
    testing, as in Learn-then-Test). Assumes the labeled rows are a random sample of the rows it will be applied to.

    Testing starts only where enough rows are accepted for the target to be provable: even with every accepted
    row correct, fewer than log(alpha) / log(target) rows can't certify it, and a failure there would end the
    sequence. The start therefore needs at least twice that many rows. That depends on row counts only, not on
    the labels, so the guarantee is unaffected.
    """
    import math
    score, correct = np.asarray(score, float), np.asarray(correct, float)
    min_accept = max(min_accept, 2 * math.ceil(math.log(alpha) / math.log(target))) if 0 < target < 1 else min_accept
    chosen, tested = None, []
    for i in range(1, grid + 1):
        t = float(np.quantile(score, 1 - i / grid))
        if tested and t == tested[-1]["threshold"]:      # tied scores: the same test again adds nothing
            continue
        acc = score >= t
        n = int(acc.sum())
        if n < min_accept:
            continue
        k = int(correct[acc].sum())
        lb = clopper_pearson_lower(k, n, alpha)
        tested.append({"threshold": t, "accepted": n, "coverage": n / len(score), "precision": k / n, "lower_bound": lb})
        if lb < target:
            break
        chosen = tested[-1]
    return {"target": target, "alpha": alpha, "min_accept": min_accept, "chosen": chosen, "tested": tested}
