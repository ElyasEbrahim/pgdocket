-- docket migration 016 (#46): answers kept under review.
-- `pgdocket calibrate NAME --review ANSWER[,ANSWER…]` stores `review`, a list of answers, in the calibration. A row
-- the view gives one of these answers is never `auto`; nothing else changes. On a yes/no decision, `--review yes`
-- keeps only the "no" band of a recall target automatic: a misleading claim written twice was accepted as "yes" on
-- 95.5 % of attacked issues, and no check stopped it without flagging over 20 % of the clean "yes" rows (#44).
-- The answer is computed once, in a lateral subquery, so `auto` can test the very answer the view shows.
CREATE OR REPLACE FUNCTION docket.create_view(p_decision bigint) RETURNS void
LANGUAGE plpgsql AS $$
DECLARE
    d docket.decision;
BEGIN
    SELECT * INTO d FROM docket.decision WHERE id = p_decision;
    EXECUTE format($v$
        CREATE OR REPLACE VIEW docket.%I AS
        SELECT s.%I AS key,
               CASE WHEN q.attempts >= 5 AND q.last_error IS NOT NULL THEN 'error'
                    WHEN q.row_key IS NOT NULL THEN 'pending'
                    WHEN j.id IS NULL THEN 'missing'
                    ELSE 'judged' END AS status,
               ans.answer,
               j.score AS raw_score,
               cal.conf AS confidence,
               CASE WHEN v.calibration IS NULL OR j.id IS NULL THEN NULL
                    WHEN v.calibration ? 'thresholds'
                    THEN coalesce(j.score >= (v.calibration->'thresholds'->>j.value)::float8, false)
                    WHEN v.spec->>'kind' = 'noul' AND v.calibration->>'recall_threshold' IS NOT NULL
                    THEN coalesce(j.score >= (v.calibration->>'threshold')::float8, false)
                         OR j.score < (v.calibration->>'recall_threshold')::float8
                    WHEN v.calibration->>'threshold' IS NULL THEN false
                    WHEN v.calibration->>'threshold_on' = 'raw score' THEN j.score >= (v.calibration->>'threshold')::float8
                    ELSE cal.conf >= (v.calibration->>'threshold')::float8 END
               AND (coalesce((v.calibration->>'harden')::boolean, false) = false
                    OR coalesce(NOT rb.flagged, false))
               AND (j.id IS NULL OR NOT coalesce(v.calibration->'review' ? ans.answer, false)) AS auto,
               j.judged_at,
               c.version,
               CASE WHEN rb.flagged THEN 'flagged' WHEN NOT rb.flagged THEN 'passed' END AS erase_check
        FROM %s s
        CROSS JOIN docket.decision d
        JOIN docket.version v ON v.decision_id = d.id AND v.version = d.active_version
        LEFT JOIN docket.current c ON c.decision_id = d.id AND c.version = d.active_version AND c.row_key = s.%I::text
        LEFT JOIN docket.judgment j ON j.id = c.judgment_id
        LEFT JOIN docket.robust rb ON rb.judgment_id = j.id
        LEFT JOIN docket.queue q ON q.decision_id = d.id AND q.version = d.active_version AND q.row_key = s.%I::text
                                AND q.purpose = 'live'
        LEFT JOIN LATERAL (SELECT docket.calibrated(j.answer, v.spec, v.calibration) AS conf) cal ON true
        LEFT JOIN LATERAL (SELECT
               CASE WHEN v.spec->>'kind' = 'noul' AND v.calibration->>'recall_threshold' IS NOT NULL
                    THEN CASE WHEN j.score >= (v.calibration->>'threshold')::float8 THEN 'yes'
                              WHEN j.score < (v.calibration->>'recall_threshold')::float8 THEN 'no'
                              WHEN cal.conf >= 0.5 THEN 'yes' ELSE 'no' END
                    WHEN v.spec->>'kind' = 'noul'
                    THEN CASE WHEN CASE WHEN v.calibration->>'threshold' IS NULL THEN cal.conf >= 0.5
                                        WHEN v.calibration->>'threshold_on' = 'raw score'
                                        THEN j.score >= (v.calibration->>'threshold')::float8
                                        ELSE cal.conf >= (v.calibration->>'threshold')::float8 END
                              THEN 'yes' ELSE 'no' END
                    ELSE j.value END AS answer) ans ON true
        WHERE d.id = %s
    $v$, 'v_' || d.name, d.key_column, d.source, d.key_column, d.key_column, d.id);
END $$;

SELECT docket.create_view(id) FROM docket.decision;
