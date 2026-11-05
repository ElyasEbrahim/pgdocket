# Counts with error bars

*2026-10-20 · issue #14 · design: [ADR 0003](../adr/0003-human-labels-are-the-oracle.md)*

"How many tickets this quarter were really about billing?" There are two naive answers:

- **The model's count.** Fast, but its systematic mistakes go straight into the number. On Kubernetes issues, Jev said 20 belonged to *testing*; the true number was 9.
- **A count over hand-labelled rows.** Unbiased, but wide unless many rows are labelled.

**Prediction-powered inference** (PPI; Angelopoulos et al., *Science* 2023) uses the model's answer on every row, plus a small *random sample* of human labels that measures and corrects the model's mistakes. The result is unbiased whatever the model's quality, with a valid confidence interval. pgdocket uses the power-tuned version **PPI++**: a weight between 0 and 1 is estimated from the data. With a useless model it falls back to the labels-only interval; with a good model it leans on the model and gets much narrower.

## Use

```bash
pgdocket label triage --from-column team --sample 150     # a random sample of labels (recorded as 'random')
pgdocket estimate triage                                  # every option
pgdocket estimate triage --answer billing --where "created_at >= '2026-07-01'"
```

```sql
SELECT * FROM docket.estimate('triage', 'billing', $$created_at >= '2026-07-01'$$, 0.95);
```

Each row reports:
- the model's own count;
- the **estimate and its interval**;
- the labels-only interval, for comparison;
- how many labels it used and how many of them had the answer;
- the weight λ;
- a `reliable` flag, the `method` (`normal`, or `betting` below the floor), and a note.

**Only labels drawn at random are used.** `label --sample N` records them as `random`. A whole column (`all`) or a hand-picked CSV (`manual`) is not a random sample, and is used only with `--include-manual`.

## Validation

**1. Simulation (in CI).** Thousands of synthetic populations with a model whose mistakes depend on the truth:
- **Coverage:** 95 % intervals contain the true share 93–97.5 % of the time.
- **Width:** never meaningfully wider than labels alone, and under 60 % of the width with a good model.
- **Bias:** a biased model count (30 % false positives) is corrected.
- **The ⚠ flag:** draws with fewer than 10 labelled examples on one side are flagged. Coverage drops to about 75 % there, while flagged-reliable draws keep ≥ 93 %.

**2. Real model errors (no API calls).** The 500 Kubernetes issues Jev judged in Phase 1 have known true labels. For each answer, 2,000 random draws of n labels were taken from those 500 ([full table](../../results/ppi-validation/report.md), `jevbench ppivalidate`):

| Answer (true count of 500) | Labels | PPI covers truth | PPI ± | Labels-only ± | Flagged ⚠ |
| --- | ---: | ---: | ---: | ---: | ---: |
| node (167) | 50 | 97.2 % | 35 | 65 | 1 % |
| node (167) | 150 | 100 % | 25 | 38 | 0 % |
| api-machinery (114) | 100 | 98.7 % | 26 | 41 | 0 % |
| scheduling (64) | 150 | 99.5 % | 17 | 27 | 0 % |
| feature request (108) | 100 | 98.2 % | 28 | 40 | 0 % |
| storage (34) | 50 | 89.6 % | 16 | 33 | 100 % |
| auth (17) | 100 | 87.0 % | 13 | 16.5 | 100 % |

- **When not flagged, the 95 % interval held 98–100 % of the time** wherever most draws qualified. That's slightly conservative here, because 50–150 labels is a large share of a 500-row population. The one weaker figure, 92.7 % for *scheduling* with 50 labels, comes from the 9 % of draws that happened to qualify.
- **It's 30–50 % narrower than labels alone.** Matching that precision with labels alone would take roughly **2–3× as many labels**.
- **Rare answers are where it struggles, and the flag says so.** For answers under ~5 % of rows, 50–100 labels rarely contain 10 examples, so coverage can fall (auth 87 %, storage 90 %). Those estimates carry ⚠ every time. When the labels contain *no* example of an answer, no interval is given at all.

**3. Live, in the ledger.** Using 80 random labels on the 300 Kubernetes issues judged in the live replay:

| Answer | Model says | Estimate | 95 % interval | Labels only | True count |
| --- | ---: | ---: | ---: | ---: | ---: |
| node | 102 | 114 | 92 – 136 | 70 – 133 | **113** |
| api-machinery | 74 | 74 | 57 – 91 | 53 – 112 | **68** |
| scheduling | 58 | 57 | 43 – 70 | 24 – 73 | **60** |
| network ⚠ | 14 | 14 | 6 – 21 | 0 – 24 | **14** |
| instrumentation ⚠ | 3 | – | cannot estimate (0 labelled examples) | – | **2** |

## Limits

- **Validity needs random labels.** If the labelled rows were chosen because they were hard, recent or interesting, the interval can be wrong. That's why `manual` labels are excluded by default.
- **The interval is a normal approximation.** It needs about 10 labelled rows with *and* without the answer. For rare answers that means more labels overall. Stratified sampling, which over-samples rare answers and weights them back, is the next improvement.
- **Built since:** below that floor, a betting interval valid at any number of labels (#30), given by `docket.estimate()` in SQL too (#32). On these 300 issues it gives every rare SIG an interval that holds its true count, instrumentation's included. See [rare answers](rare-answers.md).
- **It counts judged rows only.** Rows still `pending` in the scope are reported, not estimated.
- **A `--where` scope uses only the labels inside it,** so a narrow scope needs its own labels.
