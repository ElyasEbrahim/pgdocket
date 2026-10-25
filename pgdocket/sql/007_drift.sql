-- docket migration 007 (#22): drift checks.
-- Every run of `pgdocket drift` is recorded, so repeated checks can share one error budget (run k spends
-- alpha * 6 / (pi^2 k^2)) and the history of alarms stays in the database.
CREATE TABLE docket.drift_check (
    id           bigserial PRIMARY KEY,
    decision_id  bigint NOT NULL REFERENCES docket.decision ON DELETE CASCADE,
    version      int NOT NULL,
    kind         text NOT NULL CHECK (kind IN ('data', 'model')),
    checked_at   timestamptz NOT NULL DEFAULT now(),
    alarm        boolean NOT NULL,
    result       jsonb NOT NULL
);
CREATE INDEX drift_check_by_decision ON docket.drift_check (decision_id, version, kind, checked_at);

-- Re-ask the active version on up to p_n anchors: rows with representative labels (a random sample or the whole
-- column) that it has answered. The same seed picks the same anchors each time, a fixed panel. The re-asks are
-- probes (recorded in the ledger, never made current). Returns how many rows were queued.
CREATE OR REPLACE FUNCTION docket.drift_enqueue(p_name text, p_n int, p_seed text DEFAULT '1') RETURNS int
LANGUAGE plpgsql AS $$
DECLARE
    d docket.decision;
    n int;
BEGIN
    SELECT * INTO d FROM docket.decision WHERE name = p_name;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'docket: no decision named %', p_name;
    END IF;
    WITH anchors AS (
        SELECT c.row_key
        FROM docket.current c
        JOIN docket.label l ON l.decision_id = c.decision_id AND l.row_key = c.row_key AND l.sampling IN ('random', 'all')
        WHERE c.decision_id = d.id AND c.version = d.active_version
        ORDER BY md5(c.row_key || ':' || p_seed)
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
