**English** | [简体中文](README.zh-CN.md)

# pgdocket

[![CI](https://github.com/ElyasEbrahim/pgdocket/actions/workflows/ci.yml/badge.svg)](https://github.com/ElyasEbrahim/pgdocket/actions/workflows/ci.yml)
[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)

**Durable, versioned, calibrated AI decisions for Postgres.** Declare a semantic decision once (*"is this complaint about debt collection?"*). pgdocket keeps the answer stored as data, versioned, calibrated against your own labels, and honest about its uncertainty, in any Postgres and with any decision model.

> **Status: Phase 1 in progress.** Working: [`pgdocket check`](pgdocket/README.md) measures how trustworthy an AI decision is on *your* table; the [ledger](docs/phase1/ledger.md) stores decisions in Postgres and keeps them current as rows change; [`pgdocket estimate`](docs/phase1/counts.md) gives counts with confidence intervals; [`pgdocket compare`](docs/phase1/versions.md) tests a revised decision against the current one before switching. Not built yet: other backends' adapters, a published release. The repository also contains the design ([REPORT.md](REPORT.md), [ADRs](docs/adr/)) and the Phase 0 benchmark ([findings](docs/phase0/FINDINGS-jev-1.13.md)). Progress is tracked in [issues](https://github.com/ElyasEbrahim/pgdocket/issues) and [milestones](https://github.com/ElyasEbrahim/pgdocket/milestones).

## The problem

[pg-jev](https://github.com/realZachi/pg-jev) lets you filter rows in plain language, such as `WHERE jev(tickets, 'the customer threatens to cancel')`, using TypeSafe's Jev decision model. That's good for exploring. To rely on the answers in production, they need to be stable, comparable and trustworthy, and today they aren't:

- they disappear when the database session ends, so every session judges the whole table again;
- they change when someone adds a label option, or switches model or backend;
- they change when you simply ask again, or send the row alongside different rows;
- a score of `0.7` doesn't mean 70 %, and `count(*)` over AI labels has no error bar;
- they don't run on managed hosts like Supabase, Neon or RDS.

Model calls now cost roughly **$6–25 per million rows** (depending on text length and rows per request), so the bottleneck is no longer cost. It's whether people can trust and audit the answers.

## What Phase 0 measured

2,000 real consumer-complaint narratives, judged by jev-1.13.0 through pg-jev, for about $0.30 in API calls in total.

| Finding | Result |
| --- | --- |
| Scores are over-confident | 9-way choice ECE **0.089**; yes/no ECE **0.24–0.38** |
| ~100 labels fix most of it | Calibration on 100 labels removes **45–85 %** of the error |
| Asking again changes answers | **4.8 %** of rows change top answer across 3 identical runs |
| Neighbours change answers | Re-batching the same rows changes **~16.5 %** of answers (≈ 5× noise) |
| One row per request is better | Judging rows singly: **+2.4 pp** accuracy, +0.05 macro-F1, at 1.8× tokens |
| Adding an option moves answers | **2.5 %** of answers move to a new catch-all option |
| Scores are coarse | Probabilities come on a 0.01 grid with exact zeros |

Write-up for a general audience: [*Same row, different answer*](docs/notes/2026-10-same-row-different-answer.md). Details, methods and limits: [docs/phase0/FINDINGS-jev-1.13.md](docs/phase0/FINDINGS-jev-1.13.md).

## Install

```bash
pip install "git+https://github.com/ElyasEbrahim/pgdocket"      # Python 3.10+; installed from GitHub
pgdocket --version
```

Or as a container: `docker build -t pgdocket .` from this repository. **New here?** The [5-minute quickstart](examples/quickstart/README.md) runs Postgres, the worker and 60 synthetic support tickets with Docker, through to calibration and trying a change before switching it on. It costs well under $0.01 in API calls.

## Try it on your own data: `pgdocket check`

Point it at a table, the text to send, a question and some labels (an existing column, or a CSV you label by hand). It reports agreement, calibration, a threshold with a stated precision guarantee, consistency and cost. It's read-only, and no row text goes in the report.

```bash
pgdocket check --table public.tickets --key id --text subject,body \
  --kind choice --question "Which team should handle this ticket?" \
  --options billing,technical,security,sales --label-column team --yes
```

On two years of Kubernetes issues, routing to 10 teams (SIGs) agreed with the human labels **88.8 %** of the time. **90 % of issues could be auto-routed with precision ≥ 90 % guaranteed** at 95 % confidence, or 56 % at ≥ 95 % ([write-up](docs/phase1/kubernetes-triage.md), [report](results/check-k8s-sig/report.md)). Full usage: [pgdocket/README.md](pgdocket/README.md).

## What pgdocket does, and will do

1. **Stored decisions.** *Built.* Answers live in an append-only ledger and a view you can join and index. New and changed rows are judged automatically, one row per request. 100,000 rows: one row reads in 0.14 ms; on a live stream, the median time from insert to answer was 0.7 s ([details](docs/phase1/ledger.md)).
2. **Calibration on your labels.** *Built.* About 100–200 human labels per decision turn raw scores into probabilities that mean what they say. The view shows the calibrated confidence and an `auto` flag.
3. **Honest numbers.** *Built.* Thresholds come with stated guarantees ("precision ≥ 90 % at 95 % confidence"). Answers that fall short of the target are flagged one by one, and `--per-answer` gives each answer its own guarantee. For yes/no decisions, a recall target also lets rows be answered *no* automatically while keeping, say, 90 % of the real yeses ([details](docs/phase1/recall.md)). `pgdocket plan` says how many rows people must review for the whole column to be at most, say, 5 % wrong ([details](docs/phase1/review-planning.md)). Counts come with confidence intervals: `pgdocket estimate` combines the model's answers with a random sample of labels (prediction-powered inference). On real Kubernetes issues its intervals held 98–100 % of the time and were 30–50 % narrower than labels alone ([details](docs/phase1/counts.md)).
4. **Versions.** *Built.* Changing the question, options or model creates a shadow version. `pgdocket compare` judges a sample under both versions and reports which answers changed beyond run-to-run noise. It also reports which version is more accurate. Only the rows where the versions disagree need labels for that. On Kubernetes routing, dropping the option descriptions saved 14 % of tokens and moved 3.3 % of answers beyond noise. Labelling the 13 disagreements showed the descriptions were worth keeping (v1 right 8 times, v2 twice). With all labels, they also let 95 % of issues be auto-routed at the guaranteed 90 % precision, instead of 90 %. `promote` recalibrates the new version, so automation carries over ([details](docs/phase1/versions.md)).
5. **Drift checks.** *Built.* `pgdocket drift` checks the two assumptions behind every guarantee. The first is that recent rows still look like the earlier ones; this check is free, from the answers already stored. The second is that the model still answers labelled rows as it did; this check asks a fixed panel of them again. Alarms share one false-alarm budget across repeated runs. On stored Jev answers, false alarms stayed at 0–2.6 %, and real shifts of 13–17 % in CFPB and Kubernetes labels were caught. Removing the option descriptions was caught from the model's scores ([details](docs/phase1/drift.md)).
6. **Runs anywhere.** *Built and tested on PostgreSQL 14–18 as a non-superuser.* Plain SQL plus an external worker, no untrusted extension. Not yet tested on Supabase, Neon or RDS themselves.

Architecture and SQL sketch: [REPORT.md §4](REPORT.md#4-the-recommended-project-docket). Decisions so far: [docs/adr/](docs/adr/).

## Quick start: the Phase 0 harness

Requires Docker. Everything runs in containers.

```bash
make up            # Postgres 16 + pg-jev v0.2.1, pg-jev's mock API, a simulated backend
make test          # harness unit tests
make smoke         # pg-jev's README queries against the mock
make fetch load    # 2,000 CFPB complaint narratives (CC0 mirror) → Postgres
make bench         # all experiments on the simulated backend → results/<run_id>/report.md
```

To run against the real Jev API, copy `.env.example` to `.env`, add your TypeSafe key, then:

```bash
make bench BACKEND=jev ARGS=--yes   # prints the estimated spend; refuses anything over $1
```

More commands (determinism, learning curve, batch context): [harness/README.md](harness/README.md).

## Repository map

| Path | What |
| --- | --- |
| [REPORT.md](REPORT.md) | Project report: pg-jev teardown, research survey, candidates, design, evaluation plan, risks |
| [ROADMAP.md](ROADMAP.md) | Phased plan with exit criteria |
| [CHANGELOG.md](CHANGELOG.md) | What changed, by date |
| [docs/adr/](docs/adr/) | Architecture decision records |
| [docs/phase0/](docs/phase0/) | Phase 0 findings, status, draft upstream issue |
| [docs/notes/](docs/notes/) | Public write-ups |
| [docs/phase1/](docs/phase1/) | Phase 1 results: the ledger, counts with error bars, version comparison, Kubernetes triage |
| [pgdocket/](pgdocket/) | The product: `pgdocket check`, the ledger and worker, `estimate`, `compare` |
| [examples/](examples/) | The [5-minute quickstart](examples/quickstart/README.md), and option files for the example checks |
| `pyproject.toml`, `Dockerfile` | Packaging: the `pgdocket` command and the worker image ([releasing](docs/RELEASING.md)) |
| [harness/](harness/) | Benchmark harness: `jevbench` CLI, simulated backend, tests |
| [results/](results/) | Benchmark reports from the runs cited in the findings |
| `docker-compose.yml`, `Makefile` | The benchmark stack and its commands |

## Sources

- [realZachi/pg-jev](https://github.com/realZachi/pg-jev), the Postgres extension this builds on (PostgreSQL License).
- winkrun, *不用向量索引，Postgres 也能做语义过滤：pg-jev 上手* (4 Oct 2026). Its list of pg-jev caveats shaped the design; the article isn't redistributed here.
- Full reference list: [REPORT.md § Sources](REPORT.md#sources).

## Contributing

This is a single-maintainer project. Issues and questions are welcome; pull requests are not accepted for now. See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

[Apache-2.0](LICENSE). Copyright 2026 ElyasEbrahim.
