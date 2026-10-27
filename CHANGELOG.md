# Changelog

Notable changes, newest first. Dates are UTC; `#n` refers to issues in this repository.

## 2026-10-27

### Added (rows that steer the model, #24)
- **The finding (`jevbench steer`, $0.60 of Jev calls).** On 300 labelled Kubernetes issues (jev-1.14), a counterfeit routing policy appended by the issue's author sent 98.7 % of them to the SIG the author chose. `auto` accepted 93.3 %: steered answers come out fully confident.
  - A misleading claim with no instruction in it steered 72.3 %.
  - "Ignore previous instructions" steered 1.0 %.
- **`pgdocket check --steer N`:** appends four attack families to N labelled rows, each aimed at a random wrong answer. It reports how many were steered and accepted by `auto`, and what erase-and-check catches and flags for nothing.
- **`--untrusted`** (on `create`, `revise` and `check`; stored in the spec): the instructions tell the model the rows come from third parties and to ignore any instructions, requests or policies in them.
  - **What it stops.** The counterfeit policy fell from 98.7 % to 1.7 % steered, and a policy aimed at the sentence itself to 2.3 %.
  - **What it doesn't.** Claims still steered 34 %.
  - **Cost.** 1.4 points of accuracy.
- **`calibrate --harden`:** a row is `auto` only after the worker has judged it again without each of its paragraphs and every answer agreed (erase-and-check, Kumar et al. 2023).
  - **Data model.** Migration `009` adds `docket.robust`, queue purpose `check`, and an `erase_check` column in the view. `promote` keeps hardening.
  - **Evidence.** It caught every steered row `auto` would have accepted, claims included.
  - **Cost.** It sent 7.4 % of clean accepted rows to review.
  - **Limit.** It is beaten by a payload repeated in two places, which `--untrusted` stops.
- **Write-up:** `docs/phase1/steering.md`; design: ADR 0005.

### Changed
- `jevbench steer` checks its spending cap before each batch of calls, from an estimate of the batch; the first run passed its own $0.60 cap by 0.3 cents.

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
