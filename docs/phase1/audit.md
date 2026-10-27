# Audits: are the answers marked `auto` still right?

*2026-10-26 · issue #23 · design: [ADR 0003](../adr/0003-human-labels-are-the-oracle.md) · lineage: harmful-shift tracking (Podkopaev & Ramdas, ICLR 2022), testing by betting (Waudby-Smith & Ramdas, JRSS-B 2024) · evidence: [results/audit-validation](../../results/audit-validation/report.md)*

`calibrate` measures precision once, on the labels it is given. The guarantee says that at least 90 % (say) of the rows marked `auto` are right, and it holds for rows like the labelled ones. The [drift check](drift.md) tells you whether the rows or the model have *changed*. But a change in the answers is not proof that accuracy fell, and a fall in accuracy may come with no visible change. Only labels on new accepted rows can show it.

`pgdocket audit` is the spot check that teams do anyway, made rigorous:

```bash
pgdocket audit triage --draw 20 --out audit.csv      # 20 rows marked auto since the last batch, to label blind
pgdocket label triage --csv audit.csv                # import the labels (kept apart as 'audit' labels)
pgdocket audit triage                                # the report; exit status 1 on an alarm
```

On toy data where the model starts routing sales tickets to billing after the first batch:

```
audit of triage v1 (calibration of 2026-10-26 10:52): 2 batches, 40 rows drawn, 40 labelled
  batch 1   2026-10-26 10:52    20 drawn    20 labelled    20 right (100.0 %)
  batch 2   2026-10-26 10:52    20 drawn    20 labelled     8 right (40.0 %)
  precision of the audited rows: 70.0 % (28 of 40)
  evidence that it fell below the 90.0 % target: e = 26 (an alarm needs 20; valid however often you look)
  ⚠ the auto-accepted answers are below the 90.0 % target (95 % confidence, from batch 2 on): stop acting on `auto`, label a fresh random sample of recent rows and recalibrate
```

## How it works

- **The draw.** Each batch is a random draw from the rows the view marks `auto` (for yes/no decisions, the auto-yes rows). It covers rows judged since the previous batch, so each batch is about new rows. Rows already labelled are left out. The draw is recorded in `docket.audit` (migration 008), together with the answer that was audited.
- **Blind labels.** The CSV has the key, the text and an empty label column, without the AI's answer or confidence. Pre-annotations pull labellers towards the model and inflate its measured accuracy (Berzak et al., EMNLP 2016; Schroeder et al., 2025). `--from-column` takes the labels from a column instead, when they already exist.
- **Kept apart.** Labels on drawn rows are recorded as `audit`. They come only from accepted rows, so calibration, counts, plans and drift anchors never use them.
- **The test.** It bets against "precision ≥ target", one audited row at a time, in draw order. Its wealth is ∏ (1 + λᵢ (target − xᵢ)), where xᵢ is 1 for a right answer.
  - **Why it holds.** While every batch's precision is at least the target, the wealth is a nonnegative supermartingale for any bet λᵢ chosen from the earlier rows. By Ville's inequality, the chance it *ever* reaches 20 is at most 5 %.
  - **What that buys.** The audit can be read after every batch, for as long as you like, at no cost in false alarms. The drift check, by contrast, splits a fixed budget across its runs. Precision may also vary from batch to batch, as long as it stays at or above the target.
- **The bets.** They follow aGRAPA (Waudby-Smith & Ramdas), sized from the last 40 audited rows. On the real CFPB stream below, a mean over every audited row was still dominated by the good months long after a change: a fall to 70 % was caught in 14 % of audits, against 93 % with the 40-row window.
- **A series per calibration.** Recalibrating changes what `auto` means, so it starts a new series, as does promoting a version.

## Evidence

No API calls: `jevbench auditvalidate` ([report](../../results/audit-validation/report.md)).

**False alarms stayed rare, read after every row.** In simulation, precision exactly at the target is the hardest case. Over 1,000 audited rows, 3.0 % of runs alarmed at a 90 % target and 1.5 % at 95 % (5 % allowed). On real streams, with every label known:
- **Kubernetes.** The live decision (calibrated at 90 %): 20 issues audited a month from March to June 2026. The months' true precision ran from 71 % (7 issues in March) to 98 %. No audit alarmed in 1,000.
- **CFPB.** 2,000 complaints, calibrated at 85 % on 2015–2021 and audited through 2022–2023 in 13 batches. Every batch's true precision was between 92.5 % and 100 %. No audit alarmed in 1,000.

**How many audited rows a fall takes to show** (simulation, median / 90th percentile):

| target | 5 points lower | 10 points lower | 15 points lower | 20 points lower |
| ---: | ---: | ---: | ---: | ---: |
| 90 % | 318 / 839 | 101 / 228 | 51 / 111 | 34 / 67 |
| 95 % | 236 / 482 | 77 / 154 | 44 / 80 | 30 / 56 |

**A fall injected into the real stream.** On the CFPB stream, from the sixth batch on, right answers were turned wrong at random until the accepted complaints were at 80, 75 or 70 % (target 85 %). 500 audits each; 146 complaints are audited after the change.

| precision after the change | alarms before the change | caught by the end | audited rows from the change to the alarm (median) |
| ---: | ---: | ---: | ---: |
| 80 % | 0.0 % | 9.8 % | 111 |
| 75 % | 0.0 % | 53.4 % | 97 |
| 70 % | 0.0 % | 92.6 % | 72 |

With 20 labels a week at a 90 % target, a 15-point fall shows in about three weeks (median 51 rows) and a 5-point fall in about four months. The report says how many audited rows a 10-point fall would take at your target.

## Limits

- **Small falls take many labels.** That is the price of a test that may be read for ever without false alarms. To find out quickly, audit more rows, or label a fresh random sample and recalibrate.
- **Skipped rows.** Rows drawn but left unlabelled are left out. That is safe only if a row is not skipped for being hard; the report says when a batch is incomplete.
- **Only accepted rows.** The audit checks the rows `auto` accepts. It says nothing about rows sent to people, or about the auto-*no* band of a recall target, whose guarantee is about yeses the threshold misses.
- **An alarm says precision fell, not why.** The drift check and `compare` help find out.

## Sources

Every source the project uses, with what it was used for: [REFERENCES.md](../../REFERENCES.md).


- Podkopaev & Ramdas. *Tracking the Risk of a Deployed Model and Detecting Harmful Distribution Shifts.* ICLR 2022. [arXiv:2110.06177](https://arxiv.org/abs/2110.06177)
- Waudby-Smith & Ramdas. *Estimating Means of Bounded Random Variables by Betting.* JRSS-B, 2024. [arXiv:2010.09686](https://arxiv.org/abs/2010.09686)
- Ramdas, Grünwald, Vovk & Shafer. *Game-Theoretic Statistics and Safe Anytime-Valid Inference.* Statistical Science, 2023. [arXiv:2210.01948](https://arxiv.org/abs/2210.01948)
- Berzak, Huang, Barbu, Korhonen & Katz. *Anchoring and Agreement in Syntactic Annotations.* EMNLP 2016. [paper](https://people.csail.mit.edu/berzak/papers/D16-1239.pdf)
- Schroeder, Roy & Kabbara. *Just Put a Human in the Loop? Investigating LLM-Assisted Annotation for Subjective Tasks.* 2025. [arXiv:2507.15821](https://arxiv.org/abs/2507.15821)
- Zhang, Cai, Yu & Simeone. *Prediction-Powered Risk Monitoring of Deployed Models for Detecting Harmful Distribution Shifts.* ICML 2026. [arXiv:2602.02229](https://arxiv.org/abs/2602.02229)
