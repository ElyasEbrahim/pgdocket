-- docket migration 013 (#35): the model check's anchors rotate.
-- Each `pgdocket drift NAME --anchors N` re-asks the next N labelled rows, in the seed's order, that this series of
-- checks (since the active version was last calibrated) has not asked yet; once every one has been asked, those asked
-- longest ago. A row's first re-ask in a series is a fresh comparison with its stored answer, so it can add to the
-- model check's evidence that never expires; re-asking a fixed panel gave each check the same rows again.
CREATE OR REPLACE FUNCTION docket.drift_enqueue(p_name text, p_n int, p_seed text DEFAULT '1') RETURNS int
LANGUAGE plpgsql AS $$
DECLARE
    d docket.decision;
    began timestamptz;
    n int;
BEGIN
    SELECT * INTO d FROM docket.decision WHERE name = p_name;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'docket: no decision named %', p_name;
    END IF;
    SELECT coalesce((calibration->>'fitted_at')::timestamptz, '-infinity') INTO began
    FROM docket.version WHERE decision_id = d.id AND version = d.active_version;
    WITH anchors AS (
        SELECT c.row_key
        FROM docket.current c
        JOIN docket.label l ON l.decision_id = c.decision_id AND l.row_key = c.row_key AND l.sampling IN ('random', 'all')
        LEFT JOIN LATERAL (SELECT max(j.judged_at) AS last FROM docket.judgment j
                           WHERE j.decision_id = c.decision_id AND j.version = c.version AND j.row_key = c.row_key
                             AND j.purpose = 'probe' AND j.judged_at > began) p ON true
        WHERE c.decision_id = d.id AND c.version = d.active_version
        ORDER BY p.last NULLS FIRST, md5(c.row_key || ':' || p_seed)
        LIMIT p_n
    ), queued AS (
        INSERT INTO docket.queue (decision_id, version, row_key, purpose)
        SELECT d.id, d.active_version, row_key, 'probe' FROM anchors
        ON CONFLICT (decision_id, version, row_key, purpose) DO NOTHING
        RETURNING 1
    )
    SELECT (SELECT count(*) FROM anchors) INTO n;
    PERFORM pg_notify('docket', d.id::text);
    RETURN n;
END $$;
