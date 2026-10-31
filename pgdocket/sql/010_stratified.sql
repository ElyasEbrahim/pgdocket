-- 010: labels drawn at random within groups (pgdocket estimate --by COL --draw, #25).
--
-- A 'stratified' label belongs to a row drawn at random among the rows sharing its value of a source column. With the
-- random labels in that group, it forms a uniform sample of the group, but not of the table: `estimate --by COL` uses it,
-- and nothing else does (calibrate, plain estimate, plan and drift keep to 'random' and 'all'). A row can be drawn for
-- several columns; `strata` names them all.
ALTER TABLE docket.label DROP CONSTRAINT label_sampling_check;
ALTER TABLE docket.label ADD CONSTRAINT label_sampling_check
    CHECK (sampling IN ('random', 'all', 'manual', 'audit', 'stratified'));
ALTER TABLE docket.label ADD COLUMN strata text[];
ALTER TABLE docket.label ADD CONSTRAINT label_strata_check
    CHECK (CASE WHEN sampling = 'stratified' THEN coalesce(cardinality(strata), 0) > 0 ELSE strata IS NULL END);

-- How a row was chosen outlives re-labelling: a row drawn at random stays 'random', a row drawn within groups stays
-- 'stratified' (and collects every column it was drawn for). Shared by every import path.
CREATE OR REPLACE FUNCTION docket.label_sampling(old_sampling text, old_strata text[], new_sampling text, new_strata text[],
                                                 OUT sampling text, OUT strata text[])
LANGUAGE sql IMMUTABLE AS $$
    SELECT CASE WHEN old_sampling = 'random' THEN 'random'
                WHEN new_sampling = 'stratified' OR old_sampling = 'stratified' THEN 'stratified'
                ELSE new_sampling END,
           CASE WHEN old_sampling = 'random' THEN NULL
                WHEN new_sampling = 'stratified' OR old_sampling = 'stratified' THEN
                    ARRAY(SELECT DISTINCT x FROM unnest(coalesce(old_strata, '{}') || coalesce(new_strata, '{}')) x ORDER BY x)
                ELSE NULL END
$$;
