-- 012: betting intervals below the floor in SQL (#32).
--
-- docket.estimate() gave a rare answer, below 10 labelled rows with or without it, the normal interval marked
-- reliable = false; the CLI replaced it with a betting interval in Python (#30). Now the function does it itself, so
-- every SQL client gets an interval that holds at any number of labels. It is the interval of pgdocket.betting.interval
-- (Waudby-Smith & Ramdas, 2024: hedged betting on the rectifier y - lambda*f, without replacement, with predictable
-- plug-in bets for confidence sequences), found by bisection instead of over the whole grid of candidate shares:
-- the capital betting that the share is higher never grows as the candidate share rises, and the capital betting that it
-- is lower never shrinks, so the shares no bet rejects form an interval, and each of its ends is found by testing a
-- dozen candidates. Integration tests check it against the Python on random cases.

-- Whether candidate share m survives the bets on one side, from the labels' per-step quantities: `up` the side that bets
-- the share is higher than m (it rejects shares too low), with the check that what the labels so far leave is not
-- below 0; otherwise the side that bets lower, with the check that it is not above 1. Step k's score z[k] in [0, 1] has,
-- were the share m, the mean a[k] + b[k] * mu, mu = (rows * m - s[k]) / r[k] the share among the rows not yet labelled.
CREATE FUNCTION docket.betting_survives(
    m float8, up boolean, n_rows float8, z float8[], a float8[], b float8[], bet float8[], s float8[], r float8[],
    thr float8, s_end float8, r_end float8
) RETURNS boolean
LANGUAGE plpgsql IMMUTABLE STRICT PARALLEL SAFE AS $$
DECLARE
    g   float8 := 0;
    lft float8;
    c   float8;
    x   float8;
    u   float8;
BEGIN
    FOR k IN 1 .. coalesce(array_length(z, 1), 0) LOOP
        lft := (n_rows * m - s[k]) / r[k];
        IF (up AND lft <= -1e-9) OR (NOT up AND lft >= 1 + 1e-9) THEN
            RETURN false;                                -- impossible given the labels so far
        END IF;
        c := a[k] + b[k] * least(1, greatest(0, lft));
        IF up THEN
            x := least(bet[k], 0.75 / greatest(c, 1e-12)) * (z[k] - c);
        ELSE
            x := -least(bet[k], 0.75 / greatest(1 - c, 1e-12)) * (z[k] - c);
        END IF;
        u := 1 + x;                                      -- g += log1p(x), computed accurately
        g := g + CASE WHEN u = 1 THEN x ELSE ln(u) - ((u - 1) - x) / u END;
        IF g >= thr THEN
            RETURN false;
        END IF;
    END LOOP;
    IF r_end > 0 THEN                                    -- what the last label leaves must be possible too
        lft := (n_rows * m - s_end) / r_end;
        IF (up AND lft <= -1e-9) OR (NOT up AND lft >= 1 + 1e-9) THEN
            RETURN false;
        END IF;
    END IF;
    RETURN true;
END $$;

-- The betting interval for the share of n_rows rows whose true answer is y = 1, from labels drawn at random without
-- replacement, in the order drawn: y the labels (0/1), f the model's answers on those rows (0/1), model_total the
-- model's answers summed over all n_rows. Returns {low, high} as shares, or NULL when every share is rejected (which
-- happens, with the true share among them, with probability at most alpha). As pgdocket.betting.interval: candidate
-- shares are k / n_rows when there are at most `grid` of them, else `grid` evenly spaced ones and the ends are widened
-- by one step.
CREATE FUNCTION docket.betting_interval(
    y float8[], f float8[], model_total float8, n_rows bigint, alpha float8 DEFAULT 0.05, grid int DEFAULT 4001
) RETURNS float8[]
LANGUAGE plpgsql IMMUTABLE STRICT PARALLEL SAFE AS $$
DECLARE
    n     int := coalesce(array_length(y, 1), 0);
    big   float8 := n_rows;
    thr   float8 := ln(2 / alpha);
    exact boolean := n_rows + 1 <= grid;
    top   bigint := CASE WHEN n_rows + 1 <= grid THEN n_rows ELSE grid - 1 END;
    step  float8 := 1.0 / (grid - 1);
    z float8[]; a float8[]; b float8[]; bet float8[]; s float8[]; r float8[];
    sy float8 := 0; sf float8 := 0; syf float8 := 0; sff float8 := 0; sz float8 := 0; ssd float8 := 0.25;
    py float8; pf float8; v float8; lam float8; muf float8;
    lo bigint; hi bigint; mid bigint; i_lo bigint; i_hi bigint;
    m_lo float8; m_hi float8;
BEGIN
    IF n >= n_rows THEN                                  -- every row labelled: the share is known
        SELECT sum(x) / big INTO m_lo FROM unnest(y) AS t(x);
        RETURN ARRAY[m_lo, m_lo];
    END IF;
    -- what doesn't depend on the candidate share, from the labels before each one: lambda (the slope of y on f, with
    -- half a disagreement each way), the score z in [0, 1], the bet size from the spread of the scores so far
    FOR k IN 1 .. n LOOP
        py := sy + 0.5; pf := sf + 0.5;
        v := (sff + 0.5) - pf * pf / k;
        lam := CASE WHEN v > 1e-12 THEN least(1, greatest(0, (syf - py * pf / k) / v)) ELSE 0 END;
        muf := (model_total - sf) / (big - (k - 1));     -- the model's share among the rows not yet labelled
        z[k] := (y[k] - lam * f[k] + lam) / (1 + lam);
        a[k] := lam * (1 - muf) / (1 + lam);
        b[k] := 1 / (1 + lam);
        s[k] := sy;
        r[k] := big - (k - 1);
        bet[k] := sqrt(2 * thr / (ln(1 + k::float8) * ssd));
        sz := sz + z[k];
        ssd := ssd + (z[k] - (0.5 + sz) / (k + 1)) * (z[k] - (0.5 + sz) / (k + 1));
        sy := sy + y[k]; sf := sf + f[k]; syf := syf + y[k] * f[k]; sff := sff + f[k] * f[k];
    END LOOP;
    -- the lower end: the smallest candidate the bets that the share is higher never reject
    IF NOT docket.betting_survives(1.0, true, big, z, a, b, bet, s, r, thr, sy, big - n) THEN
        RETURN NULL;
    END IF;
    IF docket.betting_survives(0.0, true, big, z, a, b, bet, s, r, thr, sy, big - n) THEN
        i_lo := 0;
    ELSE
        lo := 0; hi := top;
        WHILE hi - lo > 1 LOOP
            mid := (lo + hi) / 2;
            IF docket.betting_survives(CASE WHEN exact THEN mid / big ELSE mid * step END, true, big, z, a, b, bet, s, r,
                                       thr, sy, big - n) THEN
                hi := mid;
            ELSE
                lo := mid;
            END IF;
        END LOOP;
        i_lo := hi;
    END IF;
    -- the upper end: the largest candidate the bets that the share is lower never reject
    IF NOT docket.betting_survives(0.0, false, big, z, a, b, bet, s, r, thr, sy, big - n) THEN
        RETURN NULL;
    END IF;
    IF docket.betting_survives(1.0, false, big, z, a, b, bet, s, r, thr, sy, big - n) THEN
        i_hi := top;
    ELSE
        lo := 0; hi := top;
        WHILE hi - lo > 1 LOOP
            mid := (lo + hi) / 2;
            IF docket.betting_survives(CASE WHEN exact THEN mid / big ELSE mid * step END, false, big, z, a, b, bet, s,
                                       r, thr, sy, big - n) THEN
                lo := mid;
            ELSE
                hi := mid;
            END IF;
        END LOOP;
        i_hi := lo;
    END IF;
    IF i_lo > i_hi THEN
        RETURN NULL;
    END IF;
    m_lo := CASE WHEN i_lo = top THEN 1.0 WHEN exact THEN i_lo / big ELSE i_lo * step END;
    m_hi := CASE WHEN i_hi = top THEN 1.0 WHEN exact THEN i_hi / big ELSE i_hi * step END;
    IF NOT exact THEN                                    -- the true share may lie between two candidates
        m_lo := greatest(0, m_lo - step);
        m_hi := least(1, m_hi + step);
    END IF;
    RETURN ARRAY[m_lo, m_hi];
END $$;

-- docket.estimate() with a column `method`: 'normal' (PPI++'s interval) or 'betting'. Below the floor (fewer than 10
-- labelled rows with or without the answer), when every label used was drawn at random, the interval is by betting:
-- reliable, and the estimate kept inside it. The labels are taken in the order they were drawn: when they were
-- imported, then by a hash of the key. If every share is rejected (probability at most 1 - p_level), the normal
-- interval stays, with reliable = false.
DROP FUNCTION docket.estimate(text, text, text, float8, text[]);
CREATE FUNCTION docket.estimate(
    p_name text, p_answer text DEFAULT NULL, p_where text DEFAULT NULL, p_level float8 DEFAULT 0.95,
    p_sampling text[] DEFAULT ARRAY['random']
) RETURNS TABLE (
    answer text, rows_in_scope bigint, judged bigint, labels bigint, labels_with bigint,
    model_count float8, estimate float8, ci_low float8, ci_high float8,
    labels_only_low float8, labels_only_high float8, lambda float8, reliable boolean, method text, note text
)
LANGUAGE plpgsql STABLE AS $$
DECLARE
    d       docket.decision;
    spec    jsonb;
    answers text[];
    ans     text;
    z       float8 := docket.norm_ppf(0.5 + p_level / 2);
    cond    text := CASE WHEN p_where IS NULL OR btrim(p_where) = '' THEN 'true' ELSE p_where END;
    s       record;
    lam     float8;
    theta   float8;
    se      float8;
    se_l    float8;
    n_tot   float8;
    ys      float8[];
    fs      float8[];
    ci      float8[];
BEGIN
    SELECT * INTO d FROM docket.decision WHERE name = p_name;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'docket: no decision named %', p_name;
    END IF;
    SELECT v.spec INTO spec FROM docket.version v WHERE v.decision_id = d.id AND v.version = d.active_version;
    IF p_answer IS NOT NULL THEN
        answers := ARRAY[p_answer];
    ELSIF spec->>'kind' = 'noul' THEN
        answers := ARRAY['yes'];
    ELSE
        SELECT array_agg(o->>'name' ORDER BY ord) INTO answers FROM jsonb_array_elements(spec->'options') WITH ORDINALITY AS t(o, ord);
    END IF;

    FOREACH ans IN ARRAY answers LOOP
        -- One pass over the scope: f = the model said `ans` (0/1); y = the label says `ans` (0/1, labelled rows only).
        EXECUTE format($q$
            WITH scope AS (
                SELECT v.status, v.answer, s.%1$I::text AS k
                FROM %2$s s JOIN docket.%3$I v ON v.key = s.%1$I
                WHERE %4$s
            ), rows AS (
                SELECT sc.status, (sc.answer = %5$L)::int AS f, l.label IS NOT NULL AS labelled, (l.label = %5$L)::int AS y
                FROM scope sc
                LEFT JOIN docket.label l ON l.decision_id = %6$s AND l.row_key = sc.k AND l.sampling = ANY(%7$L::text[])
            )
            SELECT count(*) AS in_scope,
                   count(*) FILTER (WHERE status = 'judged') AS judged,
                   count(*) FILTER (WHERE status = 'judged' AND labelled) AS n,
                   count(*) FILTER (WHERE status = 'judged' AND NOT labelled) AS nu,
                   coalesce(sum(y) FILTER (WHERE status = 'judged' AND labelled), 0) AS k,
                   avg(f) FILTER (WHERE status = 'judged') AS f_all,
                   var_samp(f) FILTER (WHERE status = 'judged') AS var_f,
                   avg(f) FILTER (WHERE status = 'judged' AND NOT labelled) AS fu,
                   var_samp(f) FILTER (WHERE status = 'judged' AND NOT labelled) AS var_fu,
                   avg(f) FILTER (WHERE status = 'judged' AND labelled) AS fl,
                   var_samp(f) FILTER (WHERE status = 'judged' AND labelled) AS var_fl,
                   avg(y) FILTER (WHERE status = 'judged' AND labelled) AS ybar,
                   var_samp(y) FILTER (WHERE status = 'judged' AND labelled) AS var_y,
                   covar_samp(y, f) FILTER (WHERE status = 'judged' AND labelled) AS cov_yf
            FROM rows
        $q$, d.key_column, d.source, 'v_' || p_name, cond, ans, d.id, p_sampling) INTO s;

        answer := ans; rows_in_scope := s.in_scope; judged := s.judged; labels := s.n; labels_with := s.k;
        model_count := coalesce(s.f_all, 0) * s.judged;
        estimate := NULL; ci_low := NULL; ci_high := NULL; labels_only_low := NULL; labels_only_high := NULL;
        lambda := NULL; reliable := false; method := NULL; note := NULL;
        n_tot := s.judged;

        IF s.n < 2 THEN
            note := 'no random-sample labels in scope: import some with pgdocket label --sample N';
        ELSIF s.k = 0 OR s.k = s.n THEN
            note := format('no labelled rows %s this answer among %s: cannot estimate; label more rows',
                           CASE WHEN s.k = 0 THEN 'with' ELSE 'without' END, s.n);
        ELSE
            se_l := sqrt(s.var_y / s.n);
            labels_only_low := greatest(0, s.ybar - z * se_l) * n_tot;
            labels_only_high := least(1, s.ybar + z * se_l) * n_tot;
            IF s.nu < 2 THEN
                lam := 0; theta := s.ybar; se := se_l;
            ELSE
                lam := CASE WHEN coalesce(s.var_f, 0) <= 0 THEN 0
                            ELSE least(1, greatest(0, s.cov_yf / ((1 + s.n::float8 / s.nu) * s.var_f))) END;
                theta := s.ybar + lam * (s.fu - s.fl);
                se := sqrt(greatest(0, (s.var_y + lam * lam * s.var_fl - 2 * lam * s.cov_yf) / s.n
                                        + lam * lam * s.var_fu / s.nu));
            END IF;
            estimate := theta * n_tot;
            ci_low := greatest(0, theta - z * se) * n_tot;
            ci_high := least(1, theta + z * se) * n_tot;
            lambda := lam;
            method := 'normal';
            reliable := least(s.k, s.n - s.k) >= 10;
            IF NOT reliable THEN
                note := format('only %s labelled rows with and %s without this answer; the interval may be too narrow (need 10 each)',
                               s.k, s.n - s.k);
            END IF;
        END IF;
        -- Below the floor, from labels drawn at random only: the betting interval, valid at any number of labels.
        IF NOT reliable AND s.n >= 1 AND p_sampling <@ ARRAY['random']::text[] THEN
            EXECUTE format($q$
                SELECT array_agg((l.label = %5$L)::int::float8 ORDER BY l.labeled_at, md5(l.row_key)),
                       array_agg(coalesce((v.answer = %5$L)::int, 0)::float8 ORDER BY l.labeled_at, md5(l.row_key))
                FROM %2$s s JOIN docket.%3$I v ON v.key = s.%1$I
                JOIN docket.label l ON l.decision_id = %6$s AND l.row_key = s.%1$I::text AND l.sampling = ANY(%7$L::text[])
                WHERE v.status = 'judged' AND (%4$s)
            $q$, d.key_column, d.source, 'v_' || p_name, cond, ans, d.id, p_sampling) INTO ys, fs;
            ci := docket.betting_interval(ys, fs, model_count, s.judged, 1 - p_level);
            IF ci IS NOT NULL THEN
                ci_low := ci[1] * n_tot;
                ci_high := ci[2] * n_tot;
                estimate := least(greatest(coalesce(estimate, s.k::float8 / s.n * n_tot), ci_low), ci_high);
                reliable := true;
                method := 'betting';
                note := NULL;
            END IF;
        END IF;
        IF s.in_scope > s.judged THEN
            note := concat_ws('; ', note, format('%s rows in scope are not judged yet and are not counted', s.in_scope - s.judged));
        END IF;
        RETURN NEXT;
    END LOOP;
END $$;
