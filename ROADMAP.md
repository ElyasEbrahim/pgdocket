# Roadmap

Assumes ~20 h/week. Each phase ends with an exit criterion. If a phase misses its criterion, stop and rethink before starting the next one.

## Phase 0: Validate (weeks 1–2)

Status as of 2026-10-17: harness built and validated; real Jev run done; the technical half of the go/no-go is met. See [docs/phase0/STATUS.md](docs/phase0/STATUS.md).

- [x] pg-jev v0.2.1 in Docker; upstream regression suite passes (PG 16, and PG 18: 4/4); README queries against the upstream mock (`make up smoke upstream-test`)
- [x] CFPB narrative sample: CFPB stopped publishing narratives on 2026-08-14, so sample from the CC0 mirror (`make fetch load`; 3,000 rows, 2015–2024, revision pinned in the manifest)
- [x] Harness: `choice_base`, two cyclic shifts, `choice_other` (label-set drift), two noul tasks; raw answers stored in `bench.results`
- [x] Metrics: ECE (equal-width and equal-mass) with bootstrap CIs, Brier/NLL, AUROC, cross-fitted temperature, isotonic and Platt recalibration, order-flip rate, first-position lift, drift and auto-accept set changes
- [x] Harness validated against a simulated backend with planted flaws (16 tests, plus a 2,000-row Docker run that recovers T ≈ 1/0.5)
- [x] Found: pg-jev's session cache ignores `jev.model` / `jev.api_url` (2,000/2,000 stale answers after switching backend); draft issue in [docs/phase0/upstream-issue-cache-key.md](docs/phase0/upstream-issue-cache-key.md)
- [x] Real run on jev-1.13.0 (`jev-20261017-122314`, ≈ $0.146): see [docs/phase0/FINDINGS-jev-1.13.md](docs/phase0/FINDINGS-jev-1.13.md)
- [x] Second backend: Laya (open weights, run locally) via `--api-url`, all six experiments at one row per request, since multi-row requests break it ([comparison](docs/phase1/backends.md), #4)
- [x] Run-to-run determinism check and label learning curve (FINDINGS §7–§8; corrected the order and drift claims)
- [x] Batch-context check (#3): ~16.5 % of answers change with batch neighbours (FINDINGS §9)
- [x] Batch size 1 (#10): +2.4 pp accuracy at 1.8× tokens; pgdocket judges one row per request (FINDINGS §10, ADR 0002)
- [ ] Optional: run-to-run stability at batch size 1 (≈ $0.05)
- [ ] Turn the draft findings into the public technical note (#2)
- [ ] File the cache-key issue upstream (after review); PR adding PostgreSQL 18 to pg-jev's CI matrix
- [ ] Open a pg-jev GitHub Discussion about persisting and calibrating judgments; talk to 5–10 potential users
- [x] Name checked and chosen: **pgdocket** (#8); "docket" clashes with the `pydocket` package

**Exit (go/no-go):** measurable miscalibration or drift on real data **and** ≥3 people say they would use durable decisions.

**Decision (2026-10-19):** the technical half is met. The demand half is **deferred, not dropped**: no real users have been reached yet, so work continues into Phase 1 on real public data that stands in for a user's table (Kubernetes issue triage, #11). The first deliverable is something people can try on their own data without a conversation (`pgdocket check`, #12). Revisit the demand gate once that check is usable and has been promoted.

## Phase 1: Ledger core (weeks 3–6)

- [x] `pgdocket check` (#12): the first piece people can run on their own data; validated against Phase 0 and run on the Kubernetes triage scenario (#11)
- [x] Migration tooling for the `docket` schema (plain SQL; PG 14–18) (#13)
- [x] Tables: `decision`, `version` (spec + `spec_hash`), `judgment` (append-only), `current`, `queue`, `label`
- [x] Triggers on source tables → `queue` (only when judged columns change; `SECURITY DEFINER`)
- [x] Worker: `SKIP LOCKED` claiming with lease and token, LISTEN/NOTIFY + polling, one row per request, retries, dead-letter, spend cap
- [x] Backend adapters: Jev and any `/v1/systemone` server (mock, simulator)
- [x] `create_decision`, `backfill`, `docket.v_<name>` views with a `pending` state
- [x] Calibration in the database (labels from a column or CSV; parametric calibrator; guaranteed threshold)
- [x] CI: PostgreSQL 14–18 as a non-superuser
- [ ] Smoke test on a real Supabase / Neon project (needs an account)

**Exit:** a 100k-row table judged once and read in milliseconds; works unchanged on Supabase. *Met for the first part (0.14 ms reads at 100,000 rows); Supabase itself not yet tested, though CI runs with the same no-superuser permissions.*

## Phase 2: Trust layer (weeks 7–10)

- [x] `docket.label` with sampling recorded (`random` / `all` / `manual`); import from a column or CSV (#13, #14)
- [ ] Stratified review queue with inclusion probabilities
- [x] **Precision per answer** (#19): reported with flags in `calibrate`, `check`, `compare`; `--per-answer` guarantee applied by the view. The overall guarantee hid answers right only 54 % of the time ([review](docs/research/2026-10-frontier-review.md))
- [x] **`pgdocket plan`** (#20): how much review keeps the whole column's error ≤ ε (probably approximately correct labels), with the review queue
- [x] Calibrators (temperature with clamped zeros, Platt) fitted from labels, stored on the version, applied in SQL by `docket.calibrated()` (#13)
- [x] Precision threshold with a guarantee (fixed-sequence testing, Clopper–Pearson) in `check` and `calibrate`
- [x] Recall targets for yes/no decisions: auto-yes, review band, auto-no (#21). Adaptive labelling as in BARGAIN (SIGMOD 2026) is a later step
- [x] `docket.estimate()` / `pgdocket estimate`: PPI++ in SQL, with a reliability flag and refusal when labels in scope can't support an interval (#14)
- [ ] Review-budget planner: labels needed for a target CI width or guarantee
- [x] **Coverage-test suite** (simulation) in CI for the threshold guarantee and the count intervals
- [ ] Stratified label sampling (rare answers estimable with fewer labels); stratified PPI is the next candidate. Uncertainty-guided labelling was tested and broke coverage (#18)

**Exit:** all coverage tests pass at their stated rates.

## Phase 3: Multi-backend + drift (weeks 11–12)

- [x] Any `/v1/systemone` server, measured with Laya run locally; TypeSafe's key goes to TypeSafe's hosts only (#4)
- [ ] Adapters: OpenAI Decisions API (a different contract), Jev via OpenRouter
- [x] Shadow versions, `revise`, `compare` (changes against the re-ask noise baseline, accuracy from labels on the disagreements), gated `promote`, `retire` (#15)
- [x] Calibration in `compare` (error, guaranteed threshold, auto-accept share per version); `promote` recalibrates; thresholds on the model's own score (#16)
- [x] `pgdocket check` chooses its threshold on the raw score too, like `calibrate`; saved reports re-analysed (#16)
- [x] Backend comparison report (accuracy, ECE, cost, latency) on the same gold set: Jev and Laya on Kubernetes issues and CFPB complaints (`jevbench backends`, [write-up](docs/phase1/backends.md), #4)
- [ ] Option-order debiasing for `choice` (on only if Phase 0 showed it helps): no gain for Jev; for Laya, +1.6 points [0.7, 2.6] at 3× the calls ([backends.md](docs/phase1/backends.md)), so a per-backend option if anyone runs Laya
- [x] Rows that steer the model: `check --steer` measures it; `--untrusted` tells the model to ignore instructions in rows; `calibrate --harden` keeps a row out of `auto` until erase-and-check passes it (#24)
- [ ] Keep-one voting against payloads repeated across a row, and the two defences measured together
- [x] `pgdocket drift`: the rows (free: answer shares and scores, recent against earlier) and the model (a fixed panel of labelled rows asked again), one false-alarm budget across repeated checks (#22)
- [x] `pgdocket audit`: blind spot checks of the rows marked `auto`, with an anytime-valid alarm (an e-process; Podkopaev & Ramdas) (#23)
- [ ] Anytime-valid drift alarms that don't lose power as checks repeat (e-processes; prediction-powered risk monitoring, ICML 2026)
- [ ] Audits with fewer labels: use the model's own scores on every accepted row (prediction-powered risk monitoring)

**Exit:** backend comparison report on two public datasets.

## Phase 4: Ship v0.1 (weeks 13–14)

- [ ] Docs site / README, quick start for self-hosted, Supabase, Neon and RDS
- [x] Package and Docker image build and install from GitHub (0.1.0a2). Not published to PyPI until there are users (decision 2026-10-23); the release workflow and docs/RELEASING.md stay ready
- [ ] SQL part listed on PGXN or database.dev
- [ ] Case study (CFPB or OSHA narratives) with reproducible notebook
- [ ] Launch post; link from the pg-jev discussion

**Exit:** v0.1.0 tagged; ≥3 external users trying it.

## Later (driven by user demand)

- Gateway mode: `/v1/systemone`-compatible proxy so pg-jev's `jev.api_url` gets a persistent cross-session cache and ledger
- Cascades through stuntd/Laya (local-first, escalate uncertain rows). Zero-shot Laya certifies too little on our tasks for this to pay; a Laya fine-tuned on the ledger's labels might
- dbt package; minimal review web UI
