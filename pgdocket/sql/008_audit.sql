-- docket migration 008 (#23): audits of auto-accepted rows.
-- A batch is a random draw from the rows the view marked `auto` since the previous batch. People label them blind,
-- and the labels are kept apart ('audit'): drawn from accepted rows only, they would bias calibration and counts.
ALTER TABLE docket.label DROP CONSTRAINT label_sampling_check;
ALTER TABLE docket.label ADD CONSTRAINT label_sampling_check
    CHECK (sampling IN ('random', 'all', 'manual', 'audit'));

CREATE TABLE docket.audit (
    decision_id  bigint NOT NULL REFERENCES docket.decision ON DELETE CASCADE,
    row_key      text NOT NULL,
    version      int NOT NULL,
    fitted_at    text NOT NULL,                 -- the calibration whose `auto` the row was drawn under
    batch        int NOT NULL,
    seq          int NOT NULL,                  -- draw order within the batch: the order the test reads labels in
    judgment_id  bigint NOT NULL REFERENCES docket.judgment ON DELETE CASCADE,   -- the answer that was audited
    drawn_at     timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (decision_id, row_key)
);
CREATE INDEX audit_by_series ON docket.audit (decision_id, version, fitted_at, batch, seq);
