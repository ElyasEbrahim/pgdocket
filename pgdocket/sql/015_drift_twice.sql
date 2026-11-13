-- docket migration 015 (#37, #38): the model check can ask rows without labels twice.
-- `pgdocket drift NAME --anchors N --probes M --twice` asks each of the M rows twice, in separate requests. Without a
-- change, a row's stored answer and its two new ones are three draws from the same model, so when exactly two agree the
-- odd one is the stored answer a third of the time; after a change it is far more often, whichever way the answers
-- moved. The model check bets on that, from each row's first pair of re-asks in a series. The worker records the second
-- ask of a pair with `pair_of`, the id of the first.

ALTER TABLE docket.queue ADD COLUMN twice boolean NOT NULL DEFAULT false;
ALTER TABLE docket.judgment ADD COLUMN pair_of bigint REFERENCES docket.judgment ON DELETE CASCADE;
CREATE INDEX judgment_pairs ON docket.judgment (pair_of) WHERE pair_of IS NOT NULL;

DROP FUNCTION docket.drift_enqueue_probes(text, int, text);
CREATE FUNCTION docket.drift_enqueue_probes(p_name text, p_m int, p_seed text DEFAULT '1', p_twice boolean DEFAULT false)
RETURNS int LANGUAGE plpgsql AS $$
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
        INSERT INTO docket.queue (decision_id, version, row_key, purpose, twice)
        SELECT d.id, d.active_version, row_key, 'probe', p_twice FROM probes
        ON CONFLICT (decision_id, version, row_key, purpose) DO UPDATE SET twice = docket.queue.twice OR EXCLUDED.twice
        RETURNING 1
    )
    SELECT (SELECT count(*) FROM probes) INTO m;
    PERFORM pg_notify('docket', d.id::text);
    RETURN m;
END $$;
