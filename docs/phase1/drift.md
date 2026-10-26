# Drift checks: have the rows or the model changed?

*2026-10-25 · issue #22 · design: [ADR 0003](../adr/0003-human-labels-are-the-oracle.md) · lineage: Failing Loudly (Rabanser et al., NeurIPS 2019), canary re-asks of API models ("Who Drifted", 2026) · evidence: [results/drift-validation](../../results/drift-validation/report.md)*

Every number pgdocket gives (thresholds, counts, review plans) rests on two assumptions:
- **New rows look like the labelled ones.** If customers start writing about a new product, the mix of answers changes, and a precision measured on last month's labels may not hold for this month's rows.
- **The model answers as it did when it was calibrated.** An API provider can change the model behind a name. TypeSafe released jev-1.14.0 on 14 October and moved `jev-latest` to it, so everyone on the alias got a new model without changing anything.

`pgdocket drift` checks both:

```bash
pgdocket drift tickets --by created_at              # the rows: free, no API calls
pgdocket drift tickets --anchors 60 --run           # also the model: re-asks 60 labelled rows (60 API calls)
```

On toy data where the last week's tickets are mostly about sales, and then after the model started routing sales tickets to the technical team:

```
$ pgdocket drift tickets --by created_at
data: 60 rows from the last 7 days (by created_at) against 240 earlier · check 1, false-alarm budget 0.0304
  answers: sales 33.3 % → 80.0 %; technical 33.3 % → 0.0 % · p = 1.9e-10  ⚠ shifted
  scores: KS distance 0.000, mean 0.900 → 0.900, p = 1
  → the recent rows don't look like the earlier ones: label a fresh random sample of recent rows, then recalibrate and re-estimate

$ pgdocket drift tickets --anchors 60 --run
60 labelled rows queued to be asked again
…
model: 60 labelled rows asked again, 0.0 days after their stored answer (median) · check 2, false-alarm budget 0.0076
  accuracy 100.0 % → 58.3 %: worse on 25, better on 0, p = 3e-08  ⚠ dropped
  scores: up on 0, down on 0, mean change +0.000, p = 1
  → the model answers worse than when it was calibrated: compare a new version, or pin another model
```

The data are the integration test's. The command exits with status 1 when either check alarms, so it can run from cron or CI. Every check is stored in `docket.drift_check` (migration 007).

## How it works

**The rows (free).** The rows judged in the last `--days` days (7 by default, counted back from the newest row) are compared with all the rows before them. Rows are dated by when they were judged, or by a timestamp column of the table with `--by`. Two tests run on what the ledger already holds:
- **Answers.** Has any answer's share changed? There is one two-proportion z-test per answer, with a Bonferroni correction over the answers; answers too rare to test are left out. The alarm names the answers that moved.
- **Scores.** Has the distribution of the score the thresholds use moved? This is a two-sample Kolmogorov–Smirnov test. Jev's scores sit on a 0.01 grid and tie often, which makes the test conservative.

Tests on a classifier's own outputs were the most reliable simple shift detectors in Rabanser et al. With many rows, tiny shifts become significant, so an alarm also needs an effect that matters: an answer's share moving by at least 5 points, or a KS distance of at least 0.10. In a side experiment, a per-answer test caught more than one chi-square test over all answers: on Kubernetes routing, a 20-point shift in one SIG with a 100-row window was caught 72 % of the time against 49 %, at the same 1.4 % false-alarm rate.

**The model (N API calls).** `--anchors N` queues N rows that have representative labels (a random sample or the whole column) to be asked again by the active version. The same seed picks the same panel each time. The re-asks are *probes*: they are recorded in the ledger but never change what the view shows. Each anchor's stored answer is then compared with its newest re-ask on the identical input; rows whose text changed since are skipped. Without a change, the old and new answers are two draws from the same model, so either is equally likely to be the right one:
- **Accuracy.** An exact one-sided sign test on the anchors where exactly one of the two answers is right. The alarm means the model now answers worse.
- **Scores.** An exact two-sided sign test on whether each anchor's score went up or down. The alarm means the model behaves differently, even if accuracy has held, so the calibration may be stale.

**Repeated checks.** Drift checks are meant to run on a schedule, and every run is another chance of a false alarm. The k-th check of a kind spends 0.05 · 6 / (π² k²) of the error budget: 0.030 for the first, 0.0076 for the second, 0.0034 for the third. These budgets sum to 0.05, so over the whole series the chance of any false alarm stays at most 5 % (a union bound). Each check splits its budget equally between its two tests. Recalibrating, the usual answer to an alarm, starts a new series; so does promoting a new version.

## Evidence

No API calls: `jevbench driftvalidate` replays the checks on stored Jev answers ([report](../../results/drift-validation/report.md)).

**False alarms stayed under 5 %.**
- **Rows.** 1,000 random splits of each task's judged rows into a recent window and the rest gave 0.8–2.6 % false alarms across four tasks (500 to 2,000 rows) and windows of 50–400 rows.
- **Model.** Real re-asks of the same model on the 300 labelled Kubernetes issues, two days after their stored answers: 0.0–0.7 % of anchor panels alarmed, at every panel size. All 300 together: 2 worse / 1 better, scores up 33 / down 33.
- **Simulation (in CI).** On 1,000 simulated decisions, 20 weekly checks of an unchanging population alarmed at least once for 4.2 %, and a year of monthly model checks for 1.9 %.

**Real shifts were caught.** The most recent rows of each sample, against everything before (the check never sees the labels):

| task | rows: earlier → recent | what the check saw | true shift in the labels | alarm |
| --- | --- | --- | ---: | --- |
| CFPB complaints, 2,000 (last 365 days) | 1,073 → 927 | *credit reporting* 51.6 % → 62.8 % | 13.5 % | yes |
| CFPB complaints, 500 (last 365 days) | 269 → 231 | *credit reporting* 44.6 % → 58.4 % | 15.7 % | yes |
| Kubernetes, feature request? (last 90 days) | 442 → 58 | *yes* 24.0 % → 8.6 % | 17.2 % | yes |
| Kubernetes, which SIG? (last 90 days) | 440 → 60 | *scheduling* 10 % → 20 % (p = 0.085) | 14.2 % | no: 60 rows were too few |

**How big a shift each window catches.** Half of a task's rows are the reference; the window is drawn from the other half with one true answer made δ more frequent. Detection rate, 9-option CFPB product question:

| recent window | δ = 5 pp | 10 pp | 15 pp | 20 pp |
| ---: | ---: | ---: | ---: | ---: |
| 100 rows | 3.8 % | 10.8 % | 29.2 % | 63.2 % |
| 200 rows | 3.2 % | 20.6 % | 60.6 % | 88.8 % |
| 400 rows | 4.6 % | 36.4 % | 83.8 % | 100.0 % |

Yes/no decisions are easier: on Kubernetes feature requests, a 100-row window caught a 20-point shift 93 % of the time.

**Model changes.**

| new answers on the 300 issues | accuracy | worse / better | scores up / down | alarm | alarm rate, 100 anchors |
| --- | --- | --- | --- | --- | ---: |
| the same model, asked again | 91.3 % → 91.0 % | 2 / 1 | 33 / 33 | no | 0.1 % |
| jev-1.14 in place of jev-1.13 | 91.3 % → 91.7 % | 1 / 2 | 43 / 50 | no | 0.3 % |
| the options without their descriptions | 91.3 % → 89.3 % | 8 / 2 | 44 / 103 | scores moved | 64.9 % |

- **The jev-latest switch was invisible,** as `compare` had found: 1.14 answers these issues like 1.13.
- **Removing the descriptions changed how sure the model is.** Accuracy fell 2 points, too few rows to prove it, but the scores moved on 147 of 300 issues (p = 1.3 × 10⁻⁶). With 200 anchors the check caught it every time.
- **A real accuracy drop needs about seven anchors' worth.** On the first check, the sign test needs about 7 more anchors answered worse than better (6 to 0 gives p = 0.016, just above the test's 0.015). That is a drop of 7 points on 100 anchors, or 3.5 points on 200. Making the real re-asks wrong on 5 % of the anchors was caught on 9 % of 100-anchor panels, and on all 200-anchor panels.

## Limits

- **Small windows catch only large shifts.** With 100 recent rows, a 10-point shift in one answer usually goes unnoticed. Choose `--days` so that the recent window holds a few hundred rows.
- **The budget shrinks with every check.** The 10th check gets 0.0003, so it needs a much larger shift than the first. Check weekly or monthly, not hourly. An anytime-valid version (e-processes, as in "Who Drifted" and prediction-powered risk monitoring) would not shrink this way. It is the natural next step if checks need to run continuously.
- **Run it on a schedule, not when something looks odd.** The budgets assume each check's timing does not depend on the data it tests.
- **The rows check sees only the model's outputs.** A change in the inputs that leaves answers and scores alone is invisible to it, and a shift in answers is not proof that accuracy fell. Only labels can show that: after an alarm, label a fresh random sample of recent rows, or keep a running [audit](audit.md) of the accepted rows.
- **Anchors are a fixed panel.** They show whether the model changed on those rows, not on every kind of row. Labels drawn at random keep the panel representative. Use more anchors when answers are rare.
- **Exchangeability assumes the stored answers were not cherry-picked.** That is why anchors use `random` and `all` labels only, never `manual` ones such as the disagreements `compare` exports.

## Sources

- Rabanser, Günnemann & Lipton. *Failing Loudly: An Empirical Study of Methods for Detecting Dataset Shift.* NeurIPS 2019. [arXiv:1810.11953](https://arxiv.org/abs/1810.11953)
- *Who Drifted: the System or the Judge? Anytime-Valid Attribution in LLM Evaluation Pipelines.* Preprint, 2026. [arXiv:2606.15474](https://arxiv.org/abs/2606.15474)
- *An Auditing Test to Detect Behavioral Shift in Language Models.* [arXiv:2410.19406](https://arxiv.org/abs/2410.19406)
- *Log Probability Tracking of LLM APIs.* [arXiv:2512.03816](https://arxiv.org/abs/2512.03816)
- *You've Changed: Detecting Modification of Black-Box Large Language Models.* [arXiv:2504.12335](https://arxiv.org/abs/2504.12335)
- Podkopaev & Ramdas. *Tracking the Risk of a Deployed Model and Detecting Harmful Distribution Shifts.* ICLR 2022. [arXiv:2110.06177](https://arxiv.org/abs/2110.06177)
- Zhang, Cai, Yu & Simeone. *Prediction-Powered Risk Monitoring of Deployed Models for Detecting Harmful Distribution Shifts.* ICML 2026. [arXiv:2602.02229](https://arxiv.org/abs/2602.02229)
