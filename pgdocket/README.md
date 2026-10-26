# pgdocket

Seven pieces work today:

- **[`pgdocket check`](#pgdocket-check)**: how trustworthy is an AI decision on your table? Read-only.
- **[The ledger](#the-ledger)**: decisions stored in Postgres and kept current as rows change.
- **[Counts with error bars](#counts-with-error-bars)**: how many rows truly have each answer, with a confidence interval.
- **[Planning human review](#planning-human-review)**: how many rows must people check so the column is at most ε wrong?
- **[Comparing versions](#comparing-versions)**: is a revised question, option set or model better than the current one?
- **[Drift checks](#drift-checks)**: have the rows or the model changed since calibration?
- **[Audits](#audits)**: are the answers marked `auto` still right?

# `pgdocket check`

**How trustworthy is an AI decision on your table?** Point `pgdocket check` at a Postgres table, the text column(s) to send, a question, and some labels. It samples rows, asks the model one row per request, and writes a report:

- **agreement** with your labels, per option, and the most common disagreements;
- **calibration**: how far the model's confidence is from reality, before and after a one-parameter calibrator fitted on your labels;
- **a threshold with a guarantee**: which answers you can accept automatically with precision ≥ your target at 95 % confidence, and a table of what each target allows;
- **precision per answer** among the accepted rows, flagging any answer that is demonstrably below the target (the guarantee is for all accepted rows together);
- **consistency** (optional): how many answers change if you ask again, or send 20 rows per request;
- **cost** for this check and projected for the whole table.

It is **read-only** on your database. It talks to any `/v1/systemone`-compatible backend (TypeSafe Jev, stuntd, laya-server) directly, so pg-jev doesn't need to be installed; managed Postgres works. **No row text appears in the report.** Per-row answers are saved next to it, keyed by your primary key.

## Usage

```bash
pip install "git+https://github.com/ElyasEbrahim/pgdocket"   # or use the Docker image
export DATABASE_URL=postgresql://...          # your database (read-only access is enough)
export TYPESAFE_API_KEY=...                   # for the Jev backend

# A choice question, with labels already in a column
pgdocket check \
  --table public.tickets --key id --text subject,body \
  --kind choice --question "Which team should handle this ticket?" \
  --options billing,technical,security,sales --label-column team \
  --sample 300 --repeat 100 --batch-test 100 --yes

# A yes/no question
pgdocket check \
  --table public.tickets --key id --text subject,body \
  --kind noul --question "the customer threatens to cancel" \
  --label-column churned --positive true --yes
```

**No labels yet?** Export a sample, label it in a spreadsheet, and read it back:

```bash
pgdocket check ... --export-sample sample.csv     # writes key, text columns, empty label
pgdocket check ... --label-file sample.csv --yes  # after filling in the label column
```

**Re-analyse without paying again**, for example with another precision target:

```bash
pgdocket report results/check-<timestamp> --precision-target 0.85
```

## Options worth knowing

| Option | Default | Meaning |
| --- | --- | --- |
| `--options-file` | – | JSON list of `{"name", "description"}`. The model sees `name: description`; labels must match `name`. Descriptions help (see `examples/`). |
| `--sample` | 300 | Labeled rows to check. 100–200 already show most of the calibration picture; more labels give tighter guarantees. |
| `--precision-target` | 0.9 | Target for the guaranteed threshold. The report also shows 95 / 90 / 85 / 80 / 75 / 70 %. |
| `--repeat N` | 0 | Ask again for N rows: how stable are the answers? |
| `--batch-test N` | 0 | Send N rows 20 per request: does batching change answers? |
| `--batch-size` | 1 | Rows per request for the main run. 1 is more accurate; see the findings. |
| `--backend` | `jev` | `jev`, `sim` (the harness simulator) or any `/v1/systemone` URL. |
| `--max-usd` | 1.0 | Refuses checks estimated above this. Paid checks also need `--yes`. |
| `--where` | – | Extra SQL filter, e.g. `"created_at > '2026-01-01'"`. |

## How the guarantee works

Thresholds are tested from strictest to loosest. Each candidate accepts a fixed share of rows (5 %, 10 %, …), chosen from the scores, not the labels. The chosen threshold is the loosest one whose precision passes a one-sided Clopper–Pearson test at 95 %, stopping at the first failure. This fixed-sequence procedure keeps the chance of *any* false claim at 5 % without a multiple-testing correction.

The threshold applies to the **model's own score**: the top probability for a choice, the yes-probability for a yes/no question. That score doesn't depend on the labels, so the test holds exactly. `pgdocket calibrate` stores the same kind of threshold, and the ledger's `auto` column applies it. Reports made before 2026-10-22 used cross-fitted calibrated scores; `pgdocket report <folder>` re-analyses them for free.

It assumes the labeled sample is representative of the rows the threshold will be applied to. If the data drifts, run the check again; on a ledger, `pgdocket drift` watches for it.

## Example reports

Real data, real model (jev-1.13.0), October 2026:

- [Kubernetes issues → which SIG should handle it](../results/check-k8s-sig/report.md): 88.8 % agreement; 90 % of issues can be auto-routed with precision ≥ 90 % guaranteed (56 % at ≥ 95 %).
- [Kubernetes issues → is it a feature request?](../results/check-k8s-feature/report.md): AUROC 0.97; 80 % precision catches 82 % of feature requests.
- [CFPB complaints → which financial product?](../results/check-cfpb-product/report.md): reproduces the Phase 0 benchmark (79.8 % vs 79.3 %).

Write-up: [docs/phase1/kubernetes-triage.md](../docs/phase1/kubernetes-triage.md).

# The ledger

Declare a decision once. Every row gets a stored answer that stays current as rows are inserted or edited, with calibrated confidence and an `auto` flag for answers above a guaranteed threshold. It's plain SQL plus an external worker, with no superuser and no extension ([ADR 0004](../docs/adr/0004-ledger-queue-and-worker.md)).

```bash
pgdocket install                                   # schema docket; needs CREATE on the database
pgdocket create triage --table shop.tickets --key id --text subject,body \
  --kind choice --question "Which team should handle this ticket?" --options-file teams.json
pgdocket worker                                    # long-running; or --once to drain the queue
pgdocket label triage --from-column team --sample 200    # or --csv labels.csv (key,label)
pgdocket calibrate triage --precision-target 0.9          # add --per-answer for one guarantee per answer
pgdocket status
```

```sql
SELECT key, status, answer, confidence, auto, judged_at
FROM docket.v_triage
WHERE status = 'judged' AND auto;            -- the answers you can act on without review
```

| Column | Meaning |
| --- | --- |
| `status` | `pending` (queued), `judged`, `missing` (not judged yet), `error` (failed 5 times; see `docket.queue.last_error`) |
| `answer` | choice: the option; yes/no: `yes` / `no` (at the calibrated threshold, or 0.5 before calibration) |
| `confidence` | calibrated probability of that option (choice) or of yes (yes/no); raw before calibration |
| `auto` | the model's own score (`raw_score`) ≥ the guaranteed threshold, the score the guarantee was tested on (with `--per-answer`: that answer's own threshold); false when no threshold reaches the target; NULL until `pgdocket calibrate` has run |

- **Triggers** queue a row only when a judged column actually changes. Application roles need no grant on the `docket` schema, because the trigger function is `SECURITY DEFINER`.
- **The worker** skips rows whose input hasn't changed, retries with back-off, and stops at `--max-usd`.
- **The ledger** (`docket.judgment`) is append-only: every answer ever given stays, with its input hash, model and time.
- **The guarantee covers the accepted rows together, so `calibrate` also checks each answer.** It names any answer whose accepted rows are demonstrably below the target. On Kubernetes routing, *testing* answers were right 7 times in 13 under an overall 90 % guarantee.
  - `--per-answer` gives each answer its own threshold and guarantee. Answers with too few labelled rows get no `auto`: at 90 %, an answer needs about 60 labelled rows before it can be tested.
  - The cost is automation: there, 48 % of issues were auto-routable instead of 90 %, each answer at ≥ 90 %. `promote` keeps the mode.
- **Yes/no decisions can also take a recall target.** `calibrate --recall-target 0.9` adds a threshold below which rows are answered *no* automatically, keeping at least 90 % of the real yeses above it (95 % confidence).
  - The view then has three bands: auto-yes, a review band, and auto-no.
  - Proving 90 % needs at least 29 labelled yeses. On Kubernetes feature requests, 62.8 % of issues could be answered *no* automatically with 400 labels. See [docs/phase1/recall.md](../docs/phase1/recall.md).
- **New decisions use jev-1.14.0 by default,** pinned. `jev-latest` moved to it, and `compare` found it matched 1.13. Existing decisions keep the model they were created with.
- **`calibrate` uses labels drawn at random or a whole column.** Hand-picked labels, such as the disagreements `compare` exports, would bias it toward hard rows. They need `--include-manual`.

Measured: 100,000 rows read in milliseconds; live median of 0.7 s from insert to answer; calibration on 200 labels. See [docs/phase1/ledger.md](../docs/phase1/ledger.md).

# Counts with error bars

`pgdocket estimate` answers "how many rows truly have this answer?" with a confidence interval. It combines the model's answer on every judged row with a **random sample** of human labels that corrects the model's mistakes (prediction-powered inference, power-tuned PPI++).

```bash
pgdocket label triage --from-column team --sample 150        # random sample, recorded as such
pgdocket estimate triage                                     # every option
pgdocket estimate triage --answer billing --where "created_at >= '2026-07-01'" --level 0.9
```

```sql
SELECT answer, model_count, estimate, ci_low, ci_high, reliable FROM docket.estimate('triage');
```

| Column | Meaning |
| --- | --- |
| `model_count` | how many judged rows the model gave this answer (biased by its mistakes) |
| `estimate`, `ci_low`, `ci_high` | PPI++ estimate of the true count, with its interval |
| `labels_only_low/high` | the interval from the labels alone, for comparison |
| `labels`, `labels_with` | labels used, and how many of them had this answer |
| `lambda` | how much the estimate leans on the model (0 = labels only, 1 = full PPI) |
| `reliable` | false when fewer than 10 labels have (or lack) the answer: the interval may be too narrow |
| `note` | why something is missing or unreliable |

- **Only labels drawn at random count.** `--sample N` records labels as random, and so does a CSV imported with `--random`. Everything else needs `--include-manual`.
- **No labelled example of an answer means no interval.** Nothing measures how often the model is wrong there.
- **Validation:** coverage tests run in CI, and on 500 real Kubernetes issues the intervals held 98–100 % when reliable and were 30–50 % narrower than labels alone. See [docs/phase1/counts.md](../docs/phase1/counts.md).

# Planning human review

`pgdocket plan` answers *how many rows must people check so that at most ε of this column is wrong?* It keeps the model's answer where it's most confident and sends the rest to people, with a 95 % guarantee on the whole column (probably approximately correct labels).

```bash
pgdocket plan triage --max-error 0.05 --export review.csv   # the review queue, least confident first
```

- **What it prints:** how wrong the AI alone is, and, for 10 %, 5 %, 3 %, 2 % and 1 %, how many rows people must review. Where a target can't be proven with the labels you have, it says how many random labels it needs: 59 for 5 %, 99 for 3 %.
- **Labels:** only random-sample labels count (`label --sample`, or a CSV imported with `--random`). Reviewers are assumed to be right.
- **Evidence:** on 300 live Kubernetes issues with 80 labels, a ≤ 10 % plan reviewed 29 issues and left the column 5.0 % wrong, checked against every human label. In simulations the guarantee failed in 0.7–2.5 % of trials (5 % allowed). See [docs/phase1/review-planning.md](../docs/phase1/review-planning.md).

# Comparing versions

Change a decision safely: add a shadow version, judge a sample under both versions, and switch only if the new one is better.

```bash
pgdocket revise triage --options-file teams-v2.json      # shadow version v2; the view keeps showing v1
pgdocket compare triage 2 --sample 300 --wait 600       # v2 judges the sample; v1 is asked again (noise baseline)
                                                         # (--wait: the running worker does it; --run: no worker running)
pgdocket compare triage 2 --export-disagreements todo.csv
pgdocket label triage --csv todo.csv                     # label only the rows where they disagree
pgdocket compare triage 2                                # re-report from stored answers
pgdocket backfill triage --version 2 && pgdocket worker --once
pgdocket promote triage 2                                # or: pgdocket retire triage 2
```

The report gives:
- the share of answers the new version changes, against the share that changes when the current version is simply asked again, and the excess with a 95 % interval;
- the most common old → new moves;
- the accuracy difference with a 95 % interval and an exact sign test on the disagreements;
- each version's calibration error and how many rows it could auto-accept at your precision target;
- tokens per row;
- a verdict: `promote`, `keep current`, `no clear difference` (with how much worse or better the new version could be) or `needs labels`.

- **Re-asks are probes.** They are stored in the ledger with `purpose = 'probe'` and never become the current answer.
- **Only disagreements need labels.** Where both versions give the same answer, both are right or both are wrong. Label every row where they disagree, and the accuracy difference is known on the whole sample.
- **`promote` waits for full coverage, then recalibrates.** It refuses while the new version hasn't answered every row the current one answers (`--force` overrides). If the old version was calibrated, the new one is fitted on the same labels and precision target in the same transaction, so `auto` keeps working.
- **Automation, not just accuracy.** With 30+ random labels, the report fits both versions on them and shows each one's calibration error, guaranteed threshold and the share of compared rows it would mark `auto`.
- **A shadow version judges new rows too.** It keeps up with new and changed rows until it is promoted or retired, and that costs tokens.

On Kubernetes routing, dropping option descriptions saved 14 % of tokens. Labelling the 13 disagreements showed the descriptions were worth keeping, and with all labels they let 95 % of issues be auto-routed instead of 90 %. See [docs/phase1/versions.md](../docs/phase1/versions.md).

# Drift checks

Every guarantee assumes that new rows look like the labelled ones and that the model answers as it did when it was calibrated. `pgdocket drift` checks both:

```bash
pgdocket drift triage --by created_at                   # the rows: last 7 days against earlier ones (free)
pgdocket drift triage --anchors 100 --wait 600          # the model: 100 labelled rows asked again (100 API calls)
pgdocket drift triage --model                           # re-report on anchors already asked again
```

- **The rows (free).** It tests whether any answer's share changed (a z-test per answer, Bonferroni over the answers) and whether the scores moved (two-sample Kolmogorov–Smirnov). An alarm also needs an answer to move 5 points, or a KS distance of 0.10. `--days` sets the recent window, and `--by` dates rows by a column of the table instead of when they were judged.
- **The model.** Anchors are rows with `random` or `all` labels; the same `--seed` gives the same panel. Each one's stored answer is compared with its re-ask on the same input: an exact sign test on accuracy, and another on whether scores went up or down. Re-asks are probes and never change the view.
- **Repeated checks share one budget.** The k-th check since the version was last calibrated spends 0.05 · 6 / (π² k²), so the chance of any false alarm over the whole series stays at most 5 %. Run it on a schedule, weekly or monthly.
- **Exit status 1 on an alarm**, for cron and CI. Every check is stored in `docket.drift_check`.

On stored Jev answers, false alarms were 0–2.6 %, a 400-row window caught a 15-point shift in one answer 84 % of the time, and the check flagged real shifts of 13–17 % in CFPB and Kubernetes labels. The accuracy test needs about 7 more anchors answered worse than better, so 200 anchors catch a 3.5-point drop. See [docs/phase1/drift.md](../docs/phase1/drift.md).

# Audits

The drift check tells you whether the rows or the model changed. Only labels on new accepted rows show whether the answers marked `auto` are still right. `pgdocket audit` is a spot check of those rows, with a test you may read after every batch:

```bash
pgdocket audit triage --draw 20 --out audit.csv          # 20 rows marked auto since the last batch, to label blind
pgdocket label triage --csv audit.csv                    # import them; they are kept apart as 'audit' labels
pgdocket audit triage                                    # the report; exit status 1 on an alarm
```

- **Blind.** The CSV has no AI answer or confidence, because pre-annotations pull labellers towards the model. `--from-column` takes labels that are already in the table.
- **Anytime-valid.** An e-process bets against "precision ≥ target". While precision stays at or above it, the chance of *ever* alarming is at most 5 %, however many batches you read. Recalibrating starts a new series.
- **Kept apart.** Audit labels come only from accepted rows, so calibration, counts, plans and drift anchors don't use them.

In simulation, false alarms were 3.0 % at exactly a 90 % target, read after every row. A 10-point fall showed after a median of 101 audited rows, and a 20-point fall after 34. On real Kubernetes and CFPB streams, no audit raised a false alarm. A fall injected into the CFPB stream, to 70 %, was caught in 93 % of audits. See [docs/phase1/audit.md](../docs/phase1/audit.md).

