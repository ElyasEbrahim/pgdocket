-- docket migration 014 (#36): the model check can also re-ask rows without labels.
-- `pgdocket drift NAME --anchors N --probes M` also re-asks the next M rows of the active version that have no
-- random or whole-column label, in the seed's order, that this series of checks (since the active version was last
-- calibrated) has not asked yet; rows judged before the series began come first, since a change after that shows
-- only against answers stored before it. Once every such row has been asked, those asked longest ago. Without labels
-- these re-asks can't show that answers got worse, but their scores can show that the model changed: each row's first
-- re-ask in a series adds to the model check's scores evidence, which then outlasts the labelled rows.

-- Re-asks are few next to a table's judgments: the model check finds them through this index.
CREATE INDEX judgment_probes ON docket.judgment (decision_id, version, judged_at) WHERE purpose = 'probe';

CREATE OR REPLACE FUNCTION docket.drift_enqueue_probes(p_name text, p_m int, p_seed text DEFAULT '1') RETURNS int
LANGUAGE plpgsql AS $$
DECLARE
    d docket.decision;
    began timestamptz;
    m int;
BEGIN
    SELECT * INTO d FROM docket.decision WHERE name = p_name;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'docket: no decision named %', p_name;
    END IF;
    SELECT coalesce((calibration->>'fitted_at')::timestamptz, '-infinity') INTO began
    FROM docket.version WHERE decision_id = d.id AND version = d.active_version;
    WITH asked AS (
        SELECT j.row_key, max(j.judged_at) AS last FROM docket.judgment j
        WHERE j.decision_id = d.id AND j.version = d.active_version AND j.purpose = 'probe' AND j.judged_at > began
        GROUP BY j.row_key
    ), probes AS (
        SELECT c.row_key
        FROM docket.current c
        JOIN docket.judgment jo ON jo.id = c.judgment_id
        LEFT JOIN asked a ON a.row_key = c.row_key
        WHERE c.decision_id = d.id AND c.version = d.active_version
          AND NOT EXISTS (SELECT 1 FROM docket.label l WHERE l.decision_id = c.decision_id AND l.row_key = c.row_key
                            AND l.sampling IN ('random', 'all'))
        ORDER BY a.last NULLS FIRST, jo.judged_at > began, md5(c.row_key || ':probe:' || p_seed)
        LIMIT p_m
    ), queued AS (
        INSERT INTO docket.queue (decision_id, version, row_key, purpose)
        SELECT d.id, d.active_version, row_key, 'probe' FROM probes
        ON CONFLICT (decision_id, version, row_key, purpose) DO NOTHING
        RETURNING 1
    )
    SELECT (SELECT count(*) FROM probes) INTO m;
    PERFORM pg_notify('docket', d.id::text);
    RETURN m;
END $$;
