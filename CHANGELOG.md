# Changelog

Notable changes, newest first. Dates are UTC; `#n` refers to issues in this repository.

## 2026-10-31

### Added (weighted shares and join fan-out, #26)
- **`pgdocket estimate NAME --weight EXPR --answer A [--by COL]`**: the share of an answer when a query counts each row by EXPR, an SQL expression on the source row `s`: a column it sums, or how often a join uses the row. The share is Σ w·y / Σ w.
  - **From labels drawn at random**, rows heavier than every unlabelled row count exactly, and the rest is PPI++ on weighted means (a Hájek estimator). With every weight 1 it is the unweighted estimate.
  - **When to trust it.** Only while no unlabelled row holds more of the weight than the interval's half-width. The floor of 10 labelled rows each way is counted by weight (Kish's effective sample size).
- **Rows drawn by weight** (`--target --draw`): sequential Poisson sampling, with a permanent random number from each row's key and `--seed`.
  - **The estimate.** Rows heavy enough to be certain count exactly. The rest give a Hájek mean of residuals, with half a disagreement added each way to the variance and a floor of 5 drawn rows each way.
  - **Reading it back.** The estimate finds the sample in the ledger as the labelled run at the top of the ranking, so rounds nest.
  - **The plan** asks for as many rows drawn by weight as the target needs: a pilot of 30, then at least a quarter more per round and at most four times as many.
  - **Data model.** Migration `011` adds the `weighted` sampling. `label` imports rows the CSV marks drawn "by weight" as such, and calibration, plain estimates and drift ignore them.
- **Validation** (`jevbench weightvalidate`, no API calls): the 2,000 judged CFPB narratives and the 21,980 complaints in the source shard that carry them word for word, over all complaints and by region.
  - **Drawing by weight** brought every query to its target (± 2 points overall, ± 5 by region) with 53–94 % fewer labels than texts drawn at random. Final intervals held 92–100 %. It needed between 13 % fewer and 67 % more labels than drawing the complaints themselves.
  - **The shortcuts failed.** The texts' own share, weights ignored, held the truth in 4 % of draws. Copies counted as labelled rows held it in 46 %, with intervals 2–9 times too narrow.
  - **Random labels** held 92 % pooled, down to 54 %, because the heaviest texts were rarely labelled. The draws the new rule trusts held 99.4 %.
  - **Not adopted:** the heaviest rows outright and the rest at random, which held 81–98 %.
  - Write-up: `docs/phase1/weights.md`.

### Changed
- **`estimate --by`: a group whose rows all carry a label, of any kind, is exact.** Before, only labels drawn at random counted.
- **Draws within groups no longer skip rows labelled from a whole column (`all`).** Skipping them made the draw non-uniform over its group.
- **Draw CSVs** (`estimate --draw`) gain a `drawn` column: "at random" or "by weight".

### Added (shares by group, #25)
- **`pgdocket estimate NAME --by COL --answer A`**: the share of an answer for each value of a source column, each group corrected with its own labels (PPI++ within the group). An all-groups line weights the groups by size. A group is flagged when the model's error there differs from the other groups' (a step-down z-test, Bonferroni across groups).
- **`--target SHARE`**: how many more labels each group needs for ± SHARE.
  - **The rule.** A pilot of 30 first; then z² × the per-label variance PPI++ leaves / target², at least 10 labelled rows with and without the answer, and never more than the group's rows (the share is then exact).
  - **The comparison.** It says how many labels drawn across the table would do the same, and which group sets that.
- **`--draw CSV`** writes those rows, drawn at random within each group, blind. **`pgdocket label --csv FILE --stratified COL`** imports them.
  - **Data model.** Migration `010` adds the `stratified` sampling and `docket.label.strata`; such labels count for `--by COL` only.
  - **Re-labelling.** How a row was chosen outlives re-labelling (`docket.label_sampling`): a row drawn at random stays random, one drawn within groups stays so and collects every column it was drawn for.
- **Validation** (`jevbench groupvalidate`, no API calls) on 2,000 CFPB complaints by US region and by year, with the stored Jev and Laya answers:
  - **Within groups.** Labels drawn within groups brought every group to ± 5 points with 24–64 % fewer labels than labels drawn across the table. The final intervals held 98–100 % either way.
  - **The model mattered less.** It saved up to 22 % of labels against labels alone, and almost none for a rarer answer, where the floor of 10 labelled rows each way set the cost.
  - **One correction for all groups** covered the four regions, where the models' errors were alike, but failed in small groups (0–94 % coverage).
  - Write-up: `docs/phase1/groups.md`.

### Added (steering on Laya, #24 and #4)
- **The steering experiment on Laya** (`jevbench steer --backend http://laya:8000/v1/systemone --model laya`): 5,100 judgments, no API cost, in `results/steer-k8s-laya`.
  - **Text appended to a long issue never reached it.** Laya keeps the first ~300 tokens of a row and drops the rest without a warning. No appended attack steered any of the 234 issues longer than 1,200 characters. On the 66 shorter ones, a counterfeit policy steered 24.2 % (Jev: 98.7 % of all issues).
  - **Written first, attacks got through:** the policy as the first paragraph steered 21.3 % of issues, a claim written first and last 28.0 %.
  - **`--untrusted` doesn't reach Laya** with ten described options. Laya fits the instructions into what the options leave of a 192-token head, so the question was cut after "Which Kubernetes SIG (special interest group)", and the sentence with it. Answers were identical with and without it.
  - **Nothing was `auto`.** Laya was right on 48.3 % of the issues, so no threshold could be certified and every steered answer would have gone to review.

- **Reported upstream** as [NandhaKishorM/laya#1425](https://github.com/NandhaKishorM/laya/issues/1425), with a runnable reproduction: with several rows in one state, Laya answers every question from the whole state.

### Changed
- CI also compiles the package on Python 3.10, the oldest version it supports; the tests run on 3.12. A Python 3.12-only f-string had slipped into the new module and was caught by hand.
- `jevbench steer` reports a backend that certifies nothing by what reaches the reviewers (steered, with and without `--untrusted`), instead of tables of zeros under `auto`.

### Fixed
- `jevbench steer`: for the policy-first attack, the pattern filter checked the issue's last paragraph instead of the planted one.

## 2026-10-30

### Added (another backend, #4)
- **Laya on your own machine** (`docker compose --profile laya up -d laya`): an open-weights decision model (Convai Innovations, Apache-2.0) serving `/v1/systemone` on CPU. The image is 1.7 GB; the weights, 2.4 GB, live in a removable Docker volume.
- **The comparison** (`jevbench backends`, no API calls): Jev and Laya on the same labelled rows, in three `check` tasks and the six Phase 0 experiments.
  - **Accuracy.** Zero-shot, Laya agreed with the labels 38.0 % of the time on Kubernetes routing (Jev 88.8 %) and 65.0 % on CFPB products (79.8 %). Its AUROC on feature requests was 0.804 (0.966).
  - **The guarantees held.** With Laya, no single threshold reached 90 % precision on any task, so `calibrate` would send everything to review. Per answer, it would still accept 36.4 % of CFPB complaints automatically (Jev: 51.2 %).
  - **Calibration makes them comparable.** On 2,000 CFPB complaints, isotonic calibration on the labels brought Laya's calibration error to 0.018, as low as Jev's 0.027, although Laya was right less often (64.4 % against 77–79 %).
  - **Option order.** 14.2 % of Laya's answers changed across three orders of the same options (Jev: 7.4 %, about 5 points of it noise). Averaging the orders raised Laya's accuracy by 1.6 points; Jev's not at all.
  - **One row per request.** In a multi-row request Laya answers every question from the whole request, so pg-jev's 20-row batches gave answers near chance (routing: 38 % → 12 %). pgdocket judges one row per request.
  - **Determinism and cost.** Laya is deterministic: two full runs of 2,000 complaints gave identical probabilities. It has no API cost and took 0.3–0.5 s per row on 12 CPU cores; Jev did about 40 rows per second.
  - Write-up: `docs/phase1/backends.md`.
- `jevbench steer --backend URL --model NAME --out DIR` runs the steering experiment against any backend. It adds a sixth attack family for models with short context windows: the policy as the first paragraph.

### Changed
- `check`, when no threshold reaches the target: the report says when even the surest answers are demonstrably below it, and suggests another model or backend instead of more labels. When some answers pass on a threshold of their own, it points to `calibrate --per-answer` and lists them under *Precision per answer*.
- `check` and the Phase 0 report show no API cost for backends other than TypeSafe's; both used to price them at Jev's list price. `check` reports now record `paid`.

### Fixed (security)
- **TypeSafe's API key went to every backend.** pgdocket put `TYPESAFE_API_KEY` in the `Authorization` header of requests to any `/v1/systemone` server, and the harness gave it to pg-jev whatever the URL. Both now send it only to TypeSafe's hosts (`typesafe.ai` and its subdomains).

### Fixed
- `jevbench run` leaves pg-jev's per-statement row cap room for rows sent twice. pg-jev sends a batch again when its request fails, and counts those rows again, so with a busy local server one failed request ended a run.

## 2026-10-27

### Added (rows that steer the model, #24)
- **The finding (`jevbench steer`, $0.74 of Jev calls).** On 300 labelled Kubernetes issues (jev-1.14), a counterfeit routing policy appended by the issue's author sent 98.7 % of them to the SIG the author chose. `auto` accepted 93.3 %: steered answers come out fully confident.
  - A misleading claim with no instruction in it steered 72.3 %.
  - "Ignore previous instructions" steered 1.0 %.
- **`pgdocket check --steer N`:** appends four attack families to N labelled rows, each aimed at a random wrong answer. It reports how many were steered and accepted by `auto`, and what erase-and-check catches and flags for nothing.
- **`--untrusted`** (on `create`, `revise` and `check`; stored in the spec): the instructions tell the model the rows come from third parties and to ignore any instructions, requests or policies in them.
  - **What it stops.** The counterfeit policy fell from 98.7 % to 1.3 % steered, and a policy aimed at the sentence itself to 2.0 %.
  - **What it doesn't.** Claims still steered 33.7 %.
  - **Cost.** 1.4 points of accuracy.
- **`calibrate --harden`:** a row is `auto` only after the worker has judged it again without each of its paragraphs and every answer agreed (erase-and-check, Kumar et al. 2023).
  - **Data model.** Migration `009` adds `docket.robust`, queue purpose `check`, and an `erase_check` column in the view. `promote` keeps hardening.
  - **Evidence.** It caught every steered row `auto` would have accepted, claims included.
  - **Cost.** It sent 7.4 % of clean accepted rows to review.
  - **Limit.** It is beaten by a payload repeated in two places, which `--untrusted` stops.
- **Both defences together** (measured for $0.14 more): every attack we tried got through `auto` on at most 0.3 % of issues, except a misleading claim written twice (7.3 %). 86.3 % of issues stayed `auto` (95.0 % with no defence), 94.6 % of them right.
- **Write-up:** `docs/phase1/steering.md`; design: ADR 0005.

### Changed
- `jevbench steer` checks its spending cap before each batch of calls, from an estimate of the batch; the first run passed its own $0.60 cap by 0.3 cents.

### Added (sources)
- **`REFERENCES.md`:** every source the project uses, grouped by the feature it shaped, each with where it is used. It opens with a table of the work behind each feature. Titles and authors of arXiv papers were checked against arXiv's metadata. Both READMEs' Sources sections now link to it.

### Fixed
- The research review cited arXiv 2510.03839 as "E-SHIFT"; it is Khan & Syed's *Technical note on Sequential Test-Time Adaptation via Martingale-Driven Fisher Prompting*. Four drift sources now name their authors.
- The steering rates with `--untrusted`, first published in e54b82a, each counted one issue too many: with the sentence alone, that issue's answer was already the attacker's target. Corrected: the policy steered 1.3 %, not 1.7 %; the claim 33.7 %, not 34.0 %.

## 2026-10-26

### Added (audits, #23)
- **`pgdocket audit NAME`:** are the answers marked `auto` still right? It exits with status 1 on an alarm.
  - **Draws:** `--draw N` picks rows at random from those marked `auto` since the previous batch (yes/no: auto-yes) and records them in `docket.audit` (migration `008`).
  - **Labelling:** `--out` writes them to label blind, with no AI answer or confidence; `--from-column` takes existing labels. Labels on drawn rows are stored as `audit` and kept out of calibration, counts, plans and drift anchors.
  - **The test:** an e-process (aGRAPA bets from the last 40 audited rows) against "precision ≥ target". By Ville's inequality the chance of ever alarming is ≤ 5 % while precision holds, however often it is read. Recalibrating starts a new series.
- **Evidence (`jevbench auditvalidate`, no API calls):**
  - Simulation: 3.0 % false alarms at exactly a 90 % target, read after every row. A 10-point fall shows after a median of 101 audited rows, a 20-point fall after 34.
  - Real streams (Kubernetes by month, CFPB by year): no false alarms.
  - A fall to 70 % injected into the CFPB stream was caught in 93 % of audits, 72 audited rows after the change (median).
  - Write-up: `docs/phase1/audit.md`.

### Tested and not adopted
- **Labels stratified by answer, for per-answer guarantees** (`jevbench stratvalidate`). It certified fewer answers than random labels: Kubernetes, 300 labels, 0.0 % auto vs 30.1 %; CFPB, 2,000 complaints, 500 labels, 2.5 % vs 44.8 %.
  - Why: each answer needs ≥ 58 labelled rows above its threshold before it can be tested at 90 %, and the small answers are mostly the inaccurate ones.
  - Random labels also serve every other feature.

## 2026-10-25

### Added (drift checks, #22)
- **`pgdocket drift NAME`:** have the rows or the model changed since calibration? It exits with status 1 on an alarm, for cron and CI.
  - **The rows (free):** recent rows (`--days`, dated by when they were judged or by `--by COLUMN`) against earlier ones. It runs a z-test per answer (Bonferroni over the answers) and a two-sample KS test on scores. An alarm also needs a 5-point move or a KS distance of 0.10.
  - **The model (`--anchors N`):** a fixed panel of labelled rows is asked again as probes. Exact sign tests on accuracy and on score direction compare each row's stored answer with its re-ask on the same input.
  - **One false-alarm budget:** the k-th check since the last calibration spends 0.05 · 6 / (π² k²). Checks are stored in `docket.drift_check` (migration `007`, with `docket.drift_enqueue`).
- **Evidence:**
  - `jevbench driftvalidate` on stored Jev answers, no API calls. False alarms were 0.8–2.6 % for the rows check and 0.0–0.7 % on real re-asks of the same model.
  - Real date splits were caught: CFPB *credit reporting* went 51.6 % → 62.8 % in the latest year (true label shift 13.5 %).
  - The jev-1.13 → 1.14 switch caused no alarm, as `compare` had found. Removing the option descriptions was caught from the scores (100 % of 200-anchor panels).
  - CI simulations check the budget over 20 weekly and 12 monthly checks.
  - Write-up: `docs/phase1/drift.md`.

### Added (recall targets, #21)
- **`calibrate --recall-target 0.9` for yes/no decisions.** Rows scored below a threshold are answered *no* automatically, while at least 90 % of the real yeses stay above it (95 % confidence).
  - Method: fixed-sequence testing upward with one-sided Clopper–Pearson bounds on the labelled yeses, following SUPG and BARGAIN.
  - With the precision threshold, the view has three bands: auto-yes, review, auto-no (migration `006`).
  - `promote` keeps the target. `check` reports what 80, 90 and 95 % recall would allow.
  - When there are too few labelled yeses, it says how many it needs (29 for 90 %).
- **Evidence:**
  - CI simulation: 3.0 % failures (5 % allowed).
  - `jevbench recallvalidate` on three real yes/no tasks: at most 3.6 % failures in 27 settings.
  - Kubernetes feature requests: 62.8 % of issues answered *no* automatically with 400 labels, keeping ≥ 90 % of feature requests.
  - Write-up: `docs/phase1/recall.md`.

### Changed
- **New decisions and checks default to jev-1.14.0** (pinned). TypeSafe moved `jev-latest` to it, and `compare` found it matched 1.13 with better raw calibration. Existing decisions keep their model.

### Fixed
- `pgdocket report` wrote `report.md` before rendering it, so a rendering error left an empty report. It now renders first.
- The quickstart listed five migrations; there are seven (eight since #23).

## 2026-10-24

### Fixed (found by a fresh-clone test: clone from GitHub, follow the README and quickstart literally)
- **`pgdocket --help` crashed** on Python 3.10 and 3.12: a `%` in the `plan` help text was read as a format code. A test now runs `--help` for the command and all 17 subcommands.
- **`calibrate` run before the worker had answered the rows ended in a stack trace.** It now says what's wrong (e.g. "only 0 labeled rows with answers… 40 rows are still queued: wait for the worker…") and exits cleanly, with an integration test.
- **Quickstart:**
  - Step 1 now shows the five migrations.
  - Step 3 says to wait about 10 seconds for the worker, with `pgdocket status`; queried straight away, tickets show as `pending`.
  - Steps 4 and 5 say when the answers are ready.

## 2026-10-22

**Release 0.1.0a2.** Everything below, from #15 and #16, plus:

### Added (review planning, #20)
- **`pgdocket plan NAME --max-error ε`:** how many rows people must review so that at most ε of the column is wrong, with 95 % confidence. The model keeps its most confident answers. It prints a table for 10 % down to 1 %, says how many random labels a target needs when it can't be proven yet, and `--export` writes the review queue, least confident first.
  - **Method:** probably approximately correct labels (Candès, Ilyas & Zrnic, 2025), with a one-sided Clopper–Pearson bound on the share of rows kept and wrong, scanned from the safest cut down.
  - **Simulation (in CI):** failures 0.7–2.5 %, against 5 % allowed.
  - **Live Kubernetes decision:** with 80 labels, a ≤ 10 % plan reviewed 29 issues and left the column 5.0 % wrong, checked against every human label. Write-up: `docs/phase1/review-planning.md`.

### Added (precision per answer, #19)
- **`calibrate`, `check`, `compare` and `promote` report precision per answer** among the auto-accepted rows, with 95 % bounds. They flag answers whose upper bound is below the target.
  - **Why:** the guarantee covers all accepted rows together and could hide weak answers. On Kubernetes routing, *testing* was right 7 times in 13 under the overall 90 % guarantee. The re-analysed report now says so and recommends what to do.
- **`calibrate --per-answer`:** one threshold and one guarantee for each answer; answers with too few labelled rows get no `auto`.
  - Migration `005`: the view looks up the answer's own threshold.
  - `promote` keeps the mode, and `compare` calibrates both versions in the active version's mode.
  - On Kubernetes routing it auto-accepts 48 % of issues, each answer at ≥ 90 %, against 90 % of issues overall.
- **Tests:**
  - a CI simulation that each answer's guarantee holds where the view applies it;
  - the view checked against the Python twin row by row;
  - `check` and `compare` naming a weak answer.

### Research (#18)
- **A review of recent work, tested on our own answers** ([docs/research/2026-10-frontier-review.md](docs/research/2026-10-frontier-review.md)). `jevbench frontier` re-runs the five offline experiments, with no API calls.
  - **The most important finding: the overall precision guarantee hides weak answers.** Under the 90 % guarantee, auto-accepted Kubernetes *testing* routes were right 54 % of the time; on CFPB, *credit card* answers 53 %. Precision per answer is next (#19).
  - **Review planning with a whole-column error guarantee (PAC labels) holds on our data** (#20).
  - **Re-running `calibrate` as labels arrive stays within its 5 % error limit** (0.6–4.2 %).
  - **Not adopted:**
    - labelling where the model is unsure, for counts: coverage fell to 45–63 % for one answer;
    - asking twice: no accuracy gain.
- **jev-1.14.0 vs 1.13.0 on Kubernetes routing ($0.026).** No change beyond run-to-run noise (1.0 % vs 1.0 %), and +0.3 pp accuracy [−0.7, +1.7]. 1.14 is better calibrated raw (0.045 vs 0.060). Kept 1.13; v3 retired.

### Fixed (#18)
- `compare` paired every row with the active version's *latest* re-ask, so a later comparison changed an earlier one's report. Each row now uses the re-ask nearest in time to the new version's answer, with a regression test.
- `compare` warns that, with fewer than 150 labels, one version getting a guarantee and the other not may be chance.
- Report text: "-0.0 pp" and "1 rows".

### Added (#17)
- `compare --wait SECONDS`: queue the comparison and wait for the worker that is already running to judge it (the normal setup); `--run` remains for when no worker runs. `revise` now suggests it.
- The quickstart has a step that tries a change before switching it on: `revise` with team descriptions (`teams.json`), `compare --wait`, then `promote`, which recalibrates. Re-run end to end on Jev, with every sample output refreshed (≈ $0.004).

### Changed (#17)
- `compare` calibrates both versions on the labels `calibrate` would use (random and whole-column), so it shows what `promote` would do.

### Fixed (calibration across versions, #16)
- **The guaranteed threshold is now chosen and applied on the model's own score.**
  - *Before:* `calibrate` chose it on cross-fitted calibrated scores, but the view applied it to the final calibrator's scores, a different scale. On real Kubernetes answers this left a threshold above every row (`auto` empty while 21.7 % coverage was reported). It could, in principle, also accept rows the guarantee never tested. Whether a threshold existed depended on the random fold assignment.
  - *Now:* the raw score doesn't depend on the labels, so the test holds exactly and the view applies what was tested. On the same labels, coverage was unchanged (95.0 %), and a held-out check gave 89.4 % precision on 94 auto-routed issues (was 89.1 % on 92).
  - *Compatibility:* calibrations stored earlier keep their old rule until refitted.
- **`promote` turned automation off:** the new version had no calibrator, so `auto` became NULL everywhere. It now refits the new version on the same labels and precision target, in the same transaction.
- **`calibrate` used hand-picked labels,** including the disagreements `compare` exports, which biases it toward hard rows. It now uses `random` and `all` labels; `--include-manual` opts in.
- **Labels for an option a version lacks** were dropped from its calibration, which inflated its precision. They now count as wrong.
- **`auto` is false, not NULL,** when a calibrated version has no threshold reaching its target.
- **`pgdocket check` uses the same rule.** It now picks its threshold and its ladder of precision targets on the model's own score, so it reports the threshold `calibrate` would store (a test checks they're equal). Tied scores are tested once.
  - The saved reports were re-analysed from their answers, with no API calls. Only the thresholds changed.
  - Kubernetes routing: 90 % precision on 90 % of issues as before, and now also 95 % on 56 %. Jev gave exactly 1.0 to 56 % of issues, 98.9 % of them right.
  - CFPB: 85 % precision on 70 % of complaints (before: none).
  - Re-analysed reports say so in their header.

### Added (#16)
- **`compare` shows automation, not just accuracy.** With 30+ random labels it calibrates both versions on them and reports calibration error, the guaranteed threshold and the share of compared rows each would mark `auto`. On Kubernetes routing, the SIG descriptions let 95.0 % of issues be auto-routed at 90 % precision, against 90.0 % without them (all 300 labels). With 80 labels, the gap is 78.3 % against 74.7 %.
- Migration `004`: the view applies thresholds marked `threshold_on: "raw score"` to `judgment.score`.
- `calibration.auto()` and `raw_score()`, Python twins of the view, checked row by row against SQL.
- A CI simulation that checks the precision guarantee where the view applies it, plus an exact-coverage assertion.

### Added (version comparison, #15)
- **`pgdocket revise` / `compare` / `backfill` / `promote` / `retire` / `versions`.** `revise` adds a shadow version (new question, options, model or backend) without changing any answer. `compare` judges a sample under the shadow version and re-asks the active one on the same rows, then reports:
  - the answers changed beyond that run-to-run noise, with a paired bootstrap interval, and the most common moves;
  - the accuracy difference;
  - tokens per row;
  - a verdict.
- **Only disagreements need labels.** Where both versions agree they are equally right, so `compare --export-disagreements` lists the unlabelled rows where they disagree. Once those are labelled, the accuracy difference is known on the whole sample. The report adds an exact sign test.
- **`promote` is gated.** It refuses while the shadow version hasn't answered every row (`--force`). `retire` stops a shadow version from judging new rows.
- Migration `003`:
  - *probe* judgments: `judgment.purpose` and `queue.purpose`; probes never become current and views ignore them;
  - `docket.compare_enqueue()`, `docket.retire()`, `docket.create_view()`;
  - `version.comparison` and `version.promoted_at`;
  - `docket.status` gains `probes_queued` and `shadow_versions`.
- **Experiment on Kubernetes routing ($0.024).**
  - Dropping the SIG descriptions saved 14 % of tokens and moved 3.3 % [1.0, 6.0] of answers beyond noise.
  - 80 random labels showed no difference. Labelling the 10 remaining disagreements (90 labels) gave the same answer as all 300 human labels: v1 right 8 times, v2 twice, −2.0 pp [−4.0, 0.0], sign test p = 0.11. The descriptions stay; v2 was retired.
  - `jevbench comparevalidate` checks the verdict against every label; write-up in `docs/phase1/versions.md`.

### Changed
- Re-labelling a row that was drawn at random keeps it in the random sample (`label.sampling` records how the row was chosen).
- Labels may name an option of any version still in use, such as one only a shadow version has.

## 2026-10-20

### Added (counts with error bars, #14)
- **`pgdocket estimate` / `docket.estimate()`:** how many judged rows truly have each answer, with a confidence interval. It uses prediction-powered inference (PPI++, power-tuned) on the model's answers plus a random sample of labels, and reports the labels-only interval for comparison, a `reliable` flag (≥ 10 labels with and without the answer) and notes. No interval when the labels contain no example on one side.
- Migration `002`: `docket.label.sampling` (`random` / `all` / `manual`); `docket.norm_ppf()`. `pgdocket label --sample` records random samples, `--random` marks a CSV as random, and `--positive` normalises yes/no labels to `yes` / `no` at import.
- Python estimator (`pgdocket/estimate.py`), with coverage tests in CI, plus integration tests checking that SQL and Python agree to 1e-9.
- `jevbench ppivalidate`: on 500 real Jev judgments with known labels, intervals held 98–100 % when reliable and were 30–50 % narrower than labels alone (`results/ppi-validation/`, `docs/phase1/counts.md`).

### Changed
- **The project is now called pgdocket (#8).** "docket" clashes with the `pydocket` package, which installs a `docket` module. Python package, command (`pgdocket …`) and GitHub repository (`ElyasEbrahim/pgdocket`; the old URL redirects) are renamed. The database schema keeps the short name `docket`, as `pg_cron` uses `cron`.

### Added
- **Packaging (v0.1.0a1):** `pyproject.toml` with the `pgdocket` command and the SQL migration bundled; `pip install "git+https://github.com/ElyasEbrahim/pgdocket"` works now. A `Dockerfile` for the worker image (non-root). `pgdocket --version`.
- **5-minute quickstart** (`examples/quickstart/`): Postgres + worker + 60 synthetic tickets; install → create → live judging → labels → calibration → `auto`. Tested end to end on Jev (~$0.001).
- **Release workflow** (`.github/workflows/release.yml`, on `v*` tags only): PyPI via trusted publishing, image to `ghcr.io/elyasebrahim/pgdocket`, GitHub pre-release. Setup in `docs/RELEASING.md`.
- **The ledger (#13).** Migration `001`: decisions, versions, an append-only judgment ledger, `current`, `queue`, `label`. Insert and update triggers (only when judged columns change; `SECURITY DEFINER`, so app roles need no grants). A per-decision view `docket.v_<name>` with status, answer, calibrated confidence and `auto`. Also `create_decision`, `backfill`, `revise`, `promote`, `drop_decision` and a `docket.status` view.
- **Worker:** claim with lease and token (`FOR UPDATE SKIP LOCKED`), skip unchanged input, one row per request, ledger + current + dequeue in one transaction, back-off and dead-letter, LISTEN/NOTIFY, spend cap.
- **Calibration in the database:** `docket label` (column or CSV), `docket calibrate` (floored temperature or Platt, guaranteed threshold), applied at read time by `docket.calibrated()`, which a test checks against the Python version exactly.
- CLI: `install`, `create`, `worker`, `label`, `calibrate`, `status`, `drop`.
- Integration tests (10) on PostgreSQL 14–18 in CI, as a non-superuser with an unprivileged app role; `make itest`.
- Demos: 100,000-row scale test (simulator) and a live Kubernetes replay on Jev (`jevbench replay`). Write-up: `docs/phase1/ledger.md`. ADR 0004.

### Fixed
- `pgdocket check` recommended calibration even when, with few labels, it didn't improve the error; it now says to label more.
- The backend client created a thread pool per call, so connections were never reused; live decision latency fell from 3.9 s to 0.7 s at the median once fixed.
- The simulator gave every non-CFPB row the same answer (it keyed on a `narrative` field only).

## 2026-10-19

### Added
- **`docket check` (#12)**, the first piece of the product: samples a Postgres table (read-only), judges rows one per request against any `/v1/systemone` backend, and reports agreement, calibration (one-parameter calibrators, zeros clamped), a precision threshold with a 95 % guarantee (Clopper–Pearson, fixed-sequence testing), a table of what each precision target allows, consistency (re-ask and batching) and cost. Labels come from a column or a hand-labelled CSV (`--export-sample` / `--label-file`). `docket report` re-analyses saved answers without API calls.
- **Kubernetes triage scenario (#11)**: 3,884 issues (2024-07 → 2026-06) with human SIG and kind labels, fetched with `jevbench.ghissues` and loaded into `demo.k8s_issues`. Results: 88.8 % routing agreement; 90 % of issues auto-routable at ≥ 90 % precision (guaranteed); 20 rows per request changed 15 % of answers. Write-up: `docs/phase1/kubernetes-triage.md`.
- `docket/` package; `docket.metrics` (moved from the harness, which keeps an alias) with Clopper–Pearson bounds and threshold selection.
- Technical note *Same row, different answer* (`docs/notes/`), the general-audience write-up of Phase 0 (#2).
- **Batch size 1 (#10).** Judging one row per request: +2.4 pp accuracy [0.9, 3.8], +0.05 macro-F1, at 1.8× tokens. ADR 0002 now defaults to one row per request. Findings §10.
- `jevbench run --batch-size` and `jevbench batchsize`.
- **Batch-context check (#3).** Re-batching the same 2,000 rows changed the top answer for ~16.5 % of rows, against ~3 % run-to-run noise (+13.6 pp [13.2, 14.1]), with a same-day control. Findings §9.
- `jevbench reorder [--seed | --restore]` and `jevbench batchcontext`, with a unit test.
- Apache-2.0 license.
- Chinese README (`README.zh-CN.md`), `CONTRIBUTING.md`, this changelog, `.env.example`, GitHub Actions CI (harness tests + compose validation) and issue templates.
- Issues #1–#9 and milestones for Phase 0 and Phase 1.

### Changed
- README rewritten: current status, Phase 0 results, quick start, repository map, language switch.
- ADR 0002 notes that judgments depend on batch composition (open question for Phase 1).

### Fixed
- Stale statements in the harness README (data source), status and roadmap (test counts, simulator numbers).
- The README pointed to a source PDF that is not in the repository; it now cites the article instead.

## 2026-10-18

### Added
- **Determinism check.** Identical requests change the top answer for 4.8 % of rows across three runs. Findings §7.
- **Label learning curve.** ~100 labels remove 45–85 % of calibration error; one-parameter calibrators beat isotonic up to 500 labels. Findings §8.
- `jevbench determinism`, `jevbench curve`, `make curve`; `SIM_LOG` request logging in the simulated backend.

### Changed
- Corrected the option-order and label-drift findings: part of those flips is run-to-run noise.
- ADR 0003: default to parametric calibration (floored temperature / Platt); isotonic once ≥ 1,000 labels exist.

### Fixed
- The `sim` service ran the copy of the code baked into its image instead of the mounted code.

## 2026-10-17

### Added
- Project proposal ([REPORT.md](REPORT.md)), [ROADMAP.md](ROADMAP.md) and ADRs 0001–0003.
- Phase 0 benchmark harness: pg-jev v0.2.1 in Docker, pg-jev's mock API, a simulated backend that is calibrated by construction, and the `jevbench` CLI with six experiments and a report generator.
- First real run on jev-1.13.0 (2,000 CFPB narratives, ≈ $0.146). Findings §1–§6.
- Draft upstream issue for pg-jev's cache key, which ignores `jev.model` and `jev.api_url`.
