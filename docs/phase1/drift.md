# Drift checks: have the rows or the model changed?

*2026-10-25, 2026-11-05 · issues #22, #33 · design: [ADR 0003](../adr/0003-human-labels-are-the-oracle.md) · lineage: Failing Loudly (Rabanser et al., NeurIPS 2019), canary re-asks of API models ("Who Drifted", 2026), universal inference (Wasserman, Ramdas & Balakrishnan, PNAS 2020) · evidence: [results/drift-validation](../../results/drift-validation/report.md)*

Every number pgdocket gives (thresholds, counts, review plans) rests on two assumptions:
- **New rows look like the labelled ones.** If customers start writing about a new product, the mix of answers changes, and a precision measured on last month's labels may not hold for this month's rows.
- **The model answers as it did when it was calibrated.** An API provider can change the model behind a name. TypeSafe released jev-1.14.0 on 14 October and moved `jev-latest` to it, so everyone on the alias got a new model without changing anything.

`pgdocket drift` checks both:

```bash
pgdocket drift tickets --by created_at              # the rows: free, no API calls
pgdocket drift tickets --anchors 60 --run           # also the model: re-asks 60 labelled rows (60 API calls)
```

On toy data where the last week's tickets are mostly about sales, then four days on with 60 more such tickets, and then after the model started routing sales tickets to the technical team:

```
$ pgdocket drift tickets --by created_at
data: 60 rows from the last 7 days (by created_at) against 240 earlier · check 1
  answers: sales 33.3 % → 80.0 %; technical 33.3 % → 0.0 % · p = 1.9e-10 (needs p < 0.0076) · evidence since 2026-10-29: 8.7e+06 on sales (needs 240)  ⚠ shifted
  scores: KS distance 0.000, mean 0.900 → 0.900, p = 1 (needs p < 0.0076) · evidence since 2026-10-29: 0.015 on scores ≤ 0.90 (needs 80)
  → the recent rows don't look like the earlier ones: label a fresh random sample of recent rows, then recalibrate and re-estimate

$ pgdocket drift tickets --by created_at                        # four days on
data: 60 rows from the last 7 days (by created_at) against 240 from before the series began (2026-10-29) · check 2
  answers: sales 33.3 % → 80.0 %; technical 33.3 % → 0.0 % · p = 1.9e-10 (needs p < 0.0019) · evidence since 2026-10-29: 3.9e+13 on technical (needs 240)  ⚠ shifted
  …

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

**The rows (free).** Rows are dated by when they were judged, or by a timestamp column of the table with `--by`. The first check after the version was last calibrated divides them at the start of its window, the last `--days` days (7 by default, counted back from the newest row). The rows before that **cut** are the reference for the whole series of checks; every row after it is watched. Each check runs two tests on half the error budget each, and alarms if either does:
- **The window test.** The recent window against the reference:
  - **Answers.** Has any answer's share changed? There is one two-proportion z-test per answer, with a Bonferroni correction over the answers; answers too rare to test are left out. The alarm names the answers that moved.
  - **Scores.** Has the distribution of the score the thresholds use moved? This is a two-sample Kolmogorov–Smirnov test. Jev's scores sit on a 0.01 grid and tie often, which makes the test conservative.
  - **Against the rows from before the series began (#33).** Before, each window was compared with everything before it. Once a shift had lasted, the shifted rows became the reference and the alarms faded, though the calibration still described the old rows.
- **The evidence (#33).** Every row since the cut, in blocks of `--days` days, adds to one e-process per answer, and per decile of the reference's scores (the share of rows scored at most it):
  - **What it weighs.** The likelihood of the rows if a change began with some block, against the maximum likelihood with no change at all (universal inference, Wasserman, Ramdas & Balakrishnan, 2020).
  - **The alternative.** A change beginning with block b has prior weight 1 / (b (b + 1)). The share before and after it are each a Krichevsky–Trofimov mixture.
  - **It never expires.** The ratio is an e-process: its chance of ever reaching 1 / α is at most α, however often the check runs. So a shift that lasts piles up evidence instead of being tested afresh against a smaller budget each time.
  - **The budget.** Answers and scores each get a quarter of the budget, with a Bonferroni correction over their e-processes.

Tests on a classifier's own outputs were the most reliable simple shift detectors in Rabanser et al. With many rows, tiny shifts become significant, so an alarm also needs an effect that matters: an answer's share moving by at least 5 points, or a KS distance of at least 0.10. In a side experiment, a per-answer test caught more than one chi-square test over all answers: on Kubernetes routing, a 20-point shift in one SIG with a 100-row window was caught 72 % of the time against 49 %, at the same 1.4 % false-alarm rate.

**The model (N API calls).** `--anchors N` queues N rows that have representative labels (a random sample or the whole column) to be asked again by the active version. The same seed picks the same panel each time. The re-asks are *probes*: they are recorded in the ledger but never change what the view shows. Each anchor's stored answer is then compared with its newest re-ask on the identical input; rows whose text changed since are skipped. Without a change, the old and new answers are two draws from the same model, so either is equally likely to be the right one:
- **Accuracy.** An exact one-sided sign test on the anchors where exactly one of the two answers is right. The alarm means the model now answers worse.
- **Scores.** An exact two-sided sign test on whether each anchor's score went up or down. The alarm means the model behaves differently, even if accuracy has held, so the calibration may be stale.

**Repeated checks.** Drift checks are meant to run on a schedule, and every run is another chance of a false alarm. Over the whole series the chance of any false alarm stays at most 5 %:
- **The window tests** of the k-th check spend 0.025 · 6 / (π² k²): 0.015 for the first, 0.0038 for the second, 0.0017 for the third. Those budgets sum to 0.025 (a union bound), split equally between answers and scores.
- **The evidence** gets the other 0.025 once, for the whole series, however often it is read.
- **The model check** is not split: its k-th check spends 0.05 · 6 / (π² k²), as before.

Recalibrating, the usual answer to an alarm, starts a new series with a new cut; so does promoting a new version.

## Evidence

No API calls: `jevbench driftvalidate` replays the checks on stored Jev answers ([report](../../results/drift-validation/report.md)).

**False alarms stayed under 5 %.**
- **Rows, one check.** 1,000 random splits of each task's judged rows into a recent window and the rest gave 0.4–1.8 % false alarms across four tasks (500 to 2,000 rows) and windows of 50–400 rows. The check as it was gave 1.2–3.1 %.
- **Rows, a series.** 12 weekly checks of 100 new rows, or 52 of 30, from an unchanging population: 0.5–4 % of 200 runs alarmed at least once, against 1–4.5 % for the check as it was.
- **Model.** Real re-asks of the same model on the 300 labelled Kubernetes issues, two days after their stored answers: 0.0–0.7 % of anchor panels alarmed, at every panel size. All 300 together: 2 worse / 1 better, scores up 33 / down 33.
- **Simulation (in CI).** On 1,000 simulated decisions, 20 weekly checks of an unchanging population alarmed at least once for 4.2 %, and a year of monthly model checks for 1.9 %.

**Real shifts were caught.** The most recent rows of each sample, against everything before (the check never sees the labels):

| task | rows: earlier → recent | what the check saw | true shift in the labels | alarm |
| --- | --- | --- | ---: | --- |
| CFPB complaints, 2,000 (last 365 days) | 1,073 → 927 | *credit reporting* 51.6 % → 62.8 % | 13.5 % | yes |
| CFPB complaints, 500 (last 365 days) | 269 → 231 | *debt collection* 15.6 % → 6.1 % | 15.7 % | yes |
| Kubernetes, feature request? (last 90 days) | 442 → 58 | *yes* 24.0 % → 8.6 % (p = 0.0081) | 17.2 % | no, by a hair: the window test now needs p < 0.0076 (as it was: yes) |
| Kubernetes, which SIG? (last 90 days) | 440 → 60 | *scheduling* 10 % → 20 % (p = 0.085) | 14.2 % | no: 60 rows were too few |

**How big a shift one check catches.** Half of a task's rows are the reference; the window is drawn from the other half with one true answer made δ more frequent. Detection rate at a first check, 9-option CFPB product question, as it is (as it was):

| recent window | δ = 5 pp | 10 pp | 15 pp | 20 pp |
| ---: | ---: | ---: | ---: | ---: |
| 100 rows | 1.8 % (3.2 %) | 7.8 % (10.6 %) | 23.2 % (31.0 %) | 50.8 % (62.0 %) |
| 200 rows | 1.4 % (3.0 %) | 13.6 % (20.8 %) | 48.2 % (57.4 %) | 83.0 % (89.4 %) |
| 400 rows | 2.4 % (2.8 %) | 25.7 % (33.3 %) | 80.9 % (85.5 %) | 99.0 % (100.0 %) |

One check alone catches less than before: its window test has half the budget. Yes/no decisions are easier: on Kubernetes feature requests, a 100-row window caught a 20-point shift 80 % of the time (90 % as it was).

**What checks catch over time (#33).** The reference was 1,000 rows, and each check's new rows were drawn from the same task's rows, 200 runs each. From a given check on, one true answer was made δ more frequent. Share of runs with an alarm by the end, as it is (as it was):

| task | weekly checks | 15-point shift from the first | 20-point shift from halfway |
| --- | --- | ---: | ---: |
| CFPB, 2,000, which product? | 12 of 100 rows | 71.5 % (54.0 %) | 72.0 % (63.5 %) |
| CFPB, 2,000, which product? | 52 of 30 rows | 62.5 % (10.5 %) | 74.0 % (3.0 %) |
| CFPB, 500, which product? | 52 of 30 rows | 79.5 % (14.5 %) | 90.5 % (3.5 %) |
| Kubernetes, which SIG? | 12 of 100 rows | 96.5 % (75.0 %) | 98.0 % (84.5 %) |
| Kubernetes, which SIG? | 52 of 30 rows | 97.0 % (41.0 %) | 99.5 % (23.5 %) |
| Kubernetes, feature request? | 52 of 30 rows | 98.0 % (57.5 %) | 100.0 % (32.0 %) |

- **The check as it was went nearly blind after half a year of weekly checks.** Its budget had shrunk to under 0.0001, and its reference had absorbed the shift. A 20-point shift arriving in week 27 was caught by week 52 in 3–32 % of runs; now 74–100 %.
- **The price is the first look.** At the first check after a change the window test has half the budget, so it alarmed 9–15 points less often. A 20-point shift at week 7 of 12 was caught at once in 20.5–56.5 % of runs, against 32–67 %.
- **Four designs were tried before choosing this one** (on the 2,000 complaints and the Kubernetes SIGs):
  - **The evidence alone** caught little at a first check (0–3 % for a 15-point shift), and needed many checks to pile up.
  - **The window test alone, against the fixed reference,** was best for a shift that begins at the first check (93 % for 15 points by check 12, at 200 rows a check), but missed late shifts in long series of small windows (1 % for 20 points from halfway, 60 checks of 30 rows).
  - **Betting against a band around the reference's share** (two-sample testing by betting, Shekhar & Ramdas), in place of universal inference: valid, and close, but behind it everywhere. For example 93 % against 97 % for a 15-point shift over 52 weekly checks of 30 rows on Kubernetes SIGs, and 67 % against 75 % for a 20-point shift from halfway on 2,000 complaints. Its bets also depend on the order rows arrive in; the counts universal inference uses don't.
  - **The window test and universal inference, half the budget each:** never far behind the best, and far ahead where the others fail.

**Model changes.**

| new answers on the 300 issues | accuracy | worse / better | scores up / down | alarm | alarm rate, 100 anchors |
| --- | --- | --- | --- | --- | ---: |
| the same model, asked again | 91.3 % → 91.0 % | 2 / 1 | 33 / 33 | no | 0.2 % |
| jev-1.14 in place of jev-1.13 | 91.3 % → 91.7 % | 1 / 2 | 43 / 50 | no | 0.3 % |
| the options without their descriptions | 91.3 % → 89.3 % | 8 / 2 | 44 / 103 | scores moved | 64.9 % |

- **The jev-latest switch was invisible,** as `compare` had found: 1.14 answers these issues like 1.13.
- **Removing the descriptions changed how sure the model is.** Accuracy fell 2 points, too few rows to prove it, but the scores moved on 147 of 300 issues (p = 1.3 × 10⁻⁶). With 200 anchors the check caught it every time.
- **A real accuracy drop needs about seven anchors' worth.** On the first check, the sign test needs about 7 more anchors answered worse than better (6 to 0 gives p = 0.016, just above the test's 0.015). That is a drop of 7 points on 100 anchors, or 3.5 points on 200. Making the real re-asks wrong on 5 % of the anchors was caught on 7 % of 100-anchor panels, and on all 200-anchor panels.

## Limits

- **Small windows catch only large shifts.** With 100 recent rows, a 10-point shift in one answer usually goes unnoticed. Choose `--days` so that the recent window holds a few hundred rows.
- **The window test's budget still shrinks with every check, the evidence's doesn't (#33).** The evidence carries a lasting shift however often the check runs, but it is slow to show a fresh one: it pays for not knowing when a change began, and for the reference's own sampling error. So a shift is usually first seen by the window test, and a long series relies on the evidence.
- **The first look after a change is weaker than before.** The window test has half the budget it had. On the real Kubernetes feature-request shift (p = 0.0081), it now just misses at a first check.
- **The model check keeps its budget.** Every model check compares re-asks with the same stored answers, and their labels say whether those were right. So one check's outcome tells about the next, and their e-values couldn't be multiplied into evidence that never expires. A union bound holds under any such dependence.
- **Keep `--days` while a series runs.** Its first check sets the evidence's blocks; later checks keep them, and change only the window.
- **Run it on a schedule, not when something looks odd.** The window tests' budgets, and the model check's, assume each check's timing does not depend on the data it tests. The evidence may be read whenever you like.
- **The rows check sees only the model's outputs.** A change in the inputs that leaves answers and scores alone is invisible to it, and a shift in answers is not proof that accuracy fell. Only labels can show that: after an alarm, label a fresh random sample of recent rows, or keep a running [audit](audit.md) of the accepted rows.
- **Anchors are a fixed panel.** They show whether the model changed on those rows, not on every kind of row. Labels drawn at random keep the panel representative. Use more anchors when answers are rare.
- **Exchangeability assumes the stored answers were not cherry-picked.** That is why anchors use `random` and `all` labels only, never `manual` ones such as the disagreements `compare` exports.

## Sources

Every source the project uses, with what it was used for: [REFERENCES.md](../../REFERENCES.md).


- Rabanser, Günnemann & Lipton. *Failing Loudly: An Empirical Study of Methods for Detecting Dataset Shift.* NeurIPS 2019. [arXiv:1810.11953](https://arxiv.org/abs/1810.11953)
- Li, Y. *Who Drifted: the System or the Judge? Anytime-Valid Attribution in LLM Evaluation Pipelines.* Preprint, 2026. [arXiv:2606.15474](https://arxiv.org/abs/2606.15474)
- Richter, He, Minervini & Kusner. *An Auditing Test To Detect Behavioral Shift in Language Models.* ICLR 2025. [arXiv:2410.19406](https://arxiv.org/abs/2410.19406)
- Chauvin, Le Merrer, Taïani & Tredan. *Log Probability Tracking of LLM APIs.* ICLR 2026. [arXiv:2512.03816](https://arxiv.org/abs/2512.03816)
- Dima, Foulds, Pan & Feldman. *You've Changed: Detecting Modification of Black-Box Large Language Models.* 2025. [arXiv:2504.12335](https://arxiv.org/abs/2504.12335)
- Podkopaev & Ramdas. *Tracking the Risk of a Deployed Model and Detecting Harmful Distribution Shifts.* ICLR 2022. [arXiv:2110.06177](https://arxiv.org/abs/2110.06177)
- Wasserman, Ramdas & Balakrishnan. *Universal Inference.* PNAS 2020. [arXiv:1912.11436](https://arxiv.org/abs/1912.11436)
- Krichevsky & Trofimov. *The performance of universal encoding.* IEEE Transactions on Information Theory, 1981. [doi:10.1109/tit.1981.1056331](https://doi.org/10.1109/tit.1981.1056331)
- Shin, Ramdas & Rinaldo. *E-detectors: a nonparametric framework for sequential change detection.* 2022. [arXiv:2203.03532](https://arxiv.org/abs/2203.03532)
- Shekhar & Ramdas. *Nonparametric Two-Sample Testing by Betting.* 2021. [arXiv:2112.09162](https://arxiv.org/abs/2112.09162)
- Zhang, Cai, Yu & Simeone. *Prediction-Powered Risk Monitoring of Deployed Models for Detecting Harmful Distribution Shifts.* ICML 2026. [arXiv:2602.02229](https://arxiv.org/abs/2602.02229)
