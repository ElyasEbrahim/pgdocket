-- 011: labels drawn by weight (pgdocket estimate --weight EXPR --target --draw, #26).
--
-- A 'weighted' label belongs to a row drawn with probability in proportion to its weight in a query: the first rows of
-- a ranking by u / w, u a permanent random number from the row's key (sequential Poisson sampling). `estimate --weight`
-- reads the sample back as the run of labelled rows at the top of that ranking, so it needs no record of the draw.
-- The labels are no sample of the table: calibration, plain estimates, review plans and drift keep to 'random' and 'all'.
ALTER TABLE docket.label DROP CONSTRAINT label_sampling_check;
ALTER TABLE docket.label ADD CONSTRAINT label_sampling_check
    CHECK (sampling IN ('random', 'all', 'manual', 'audit', 'stratified', 'weighted'));
