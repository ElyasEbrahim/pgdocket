-- docket migration 009 (#24): erase-and-check before `auto`.
-- A row's author can steer its answer with text written into the row. A hardened calibration
-- (`pgdocket calibrate --harden`) keeps a row out of `auto` until the worker has judged it again without each of
-- its pieces (erase-and-check) and every answer agreed. The result belongs to the judgment it checked, so a row
-- whose text changes is checked again.
ALTER TABLE docket.queue DROP CONSTRAINT queue_purpose_check;
ALTER TABLE docket.queue ADD CONSTRAINT queue_purpose_check CHECK (purpose IN ('live', 'probe', 'check'));

CREATE TABLE docket.robust (
    judgment_id  bigint PRIMARY KEY REFERENCES docket.judgment ON DELETE CASCADE,
    decision_id  bigint NOT NULL REFERENCES docket.decision ON DELETE CASCADE,
    version      int NOT NULL,
    row_key      text NOT NULL,
    pieces       int NOT NULL,                  -- the text split on blank lines; one piece can't be checked
    flagged      boolean NOT NULL,              -- an answer changed with a piece erased, or there was only one piece
    answers      jsonb NOT NULL,                -- [{"value", "score"}] with each piece erased, in order
    tokens       int,
    checked_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX robust_by_row ON docket.robust (decision_id, version, row_key);

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
                    ELSE j.value END AS answer,
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
                    OR coalesce(NOT rb.flagged, false)) AS auto,
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
        WHERE d.id = %s
    $v$, 'v_' || d.name, d.key_column, d.source, d.key_column, d.key_column, d.id);
END $$;

SELECT docket.create_view(id) FROM docket.decision;
