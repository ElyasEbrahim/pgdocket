# Drift checks: have the rows or the model changed?

*2026-10-25, 2026-11-05, 2026-11-06, 2026-11-08, 2026-11-12, 2026-11-13 · issues #22, #33, #34, #35, #36, #37, #38 · design: [ADR 0003](../adr/0003-human-labels-are-the-oracle.md) · lineage: Failing Loudly (Rabanser et al., NeurIPS 2019), canary re-asks of API models ("Who Drifted", 2026), universal inference (Wasserman, Ramdas & Balakrishnan, PNAS 2020), betting martingales (Waudby-Smith & Ramdas, 2024) · evidence: [results/drift-validation](../../results/drift-validation/report.md), [results/drift-model](../../results/drift-model/report.md), [results/drift-probes](../../results/drift-probes/report.md), [results/drift-twice](../../results/drift-twice/report.md), [results/drift-twice-repeats](../../results/drift-twice-repeats/report.md)*

Every number pgdocket gives (thresholds, counts, review plans) rests on two assumptions:
- **New rows look like the labelled ones.** If customers start writing about a new product, the mix of answers changes, and a precision measured on last month's labels may not hold for this month's rows.
- **The model answers as it did when it was calibrated.** An API provider can change the model behind a name. TypeSafe released jev-1.14.0 on 14 October and moved `jev-latest` to it, so everyone on the alias got a new model without changing anything.

`pgdocket drift` checks both:

```bash
pgdocket drift tickets --by created_at              # the rows: free, no API calls
pgdocket drift tickets --anchors 60 --run           # also the model: re-asks the next 60 labelled rows (60 API calls)
pgdocket drift tickets --anchors 60 --probes 100 --twice --run   # and 100 rows without labels, each asked twice (260 calls)
```

On toy data where the last week's tickets are mostly about sales, then four days on with 60 more such tickets, and then after the model started routing sales tickets to the technical team:

```
$ pgdocket drift tickets --by created_at
data: 60 rows from the last 7 days (by created_at) against 240 earlier · check 1
  answers: sales 33.3 % → 80.0 %; technical 33.3 % → 0.0 % · p = 1.9e-10 (needs p < 0.012) · evidence since 2026-10-29: 8.7e+06 on sales (needs 600)  ⚠ shifted
  scores: KS distance 0.000, mean 0.900 → 0.900, p = 1 (needs p < 0.012) · evidence since 2026-10-29: 0.015 on scores ≤ 0.90 (needs 200)
  → the recent rows don't look like the earlier ones: label a fresh random sample of recent rows, then recalibrate and re-estimate

$ pgdocket drift tickets --by created_at                        # four days on
data: 60 rows from the last 7 days (by created_at) against 240 from before the series began (2026-10-29) · check 2
  answers: sales 33.3 % → 80.0 %; technical 33.3 % → 0.0 % · p = 1.9e-10 (needs p < 0.003) · evidence since 2026-10-29: 3.9e+13 on technical (needs 600)  ⚠ shifted
  …

$ pgdocket drift tickets --anchors 60 --run
60 labelled rows queued to be asked again
…
model: 60 labelled rows asked again, 0.0 days after their stored answer (median) · check 2
  accuracy 100.0 % → 58.3 %: worse on 25, better on 0, p = 3e-08 (needs p < 0.0027) · evidence from 120 first re-asks: 1.9e+06 (needs 133)  ⚠ dropped
  scores: up on 0, down on 0, mean change +0.000, p = 1 (needs p < 0.0027) · evidence: 1 (needs 267)
  → the model answers worse than when it was calibrated: compare a new version, or pin another model
```

The data are the integration test's. The command exits with status 1 when either check alarms, so it can run from cron or CI. Every check is stored in `docket.drift_check` (migration 007).

## How it works

**The rows (free).** Rows are dated by when they were judged, or by a timestamp column of the table with `--by`. The first check after the version was last calibrated divides them at the start of its window, the last `--days` days (7 by default, counted back from the newest row). The rows before that **cut** are the reference for the whole series of checks; every row after it is watched. Each check runs two tests, the window test on 80 % of the error budget and the evidence on 20 % (#34), and alarms if either does:
- **The window test.** The recent window against the reference:
  - **Answers.** Has any answer's share changed? There is one two-proportion z-test per answer, with a Bonferroni correction over the answers; answers too rare to test are left out. The alarm names the answers that moved.
  - **Scores.** Has the distribution of the score the thresholds use moved? This is a two-sample Kolmogorov–Smirnov test. Jev's scores sit on a 0.01 grid and tie often, which makes the test conservative.
  - **Against the rows from before the series began (#33).** Before, each window was compared with everything before it. Once a shift had lasted, the shifted rows became the reference and the alarms faded, though the calibration still described the old rows.
- **The evidence (#33).** Every row since the cut, in blocks of `--days` days, adds to one e-process per answer, and per decile of the reference's scores (the share of rows scored at most it):
  - **What it weighs.** The likelihood of the rows if a change began with some block, against the maximum likelihood with no change at all (universal inference, Wasserman, Ramdas & Balakrishnan, 2020).
  - **The alternative.** A change beginning with block b has prior weight 1 / (b (b + 1)). The share before and after it are each a Krichevsky–Trofimov mixture.
  - **It never expires.** The ratio is an e-process: its chance of ever reaching 1 / α is at most α, however often the check runs. So a shift that lasts piles up evidence instead of being tested afresh against a smaller budget each time.
  - **The budget.** Answers and scores each get a tenth of the budget, 0.005, with a Bonferroni correction over their e-processes.

Tests on a classifier's own outputs were the most reliable simple shift detectors in Rabanser et al. With many rows, tiny shifts become significant, so an alarm also needs an effect that matters: an answer's share moving by at least 5 points, or a KS distance of at least 0.10. In a side experiment, a per-answer test caught more than one chi-square test over all answers: on Kubernetes routing, a 20-point shift in one SIG with a 100-row window was caught 72 % of the time against 49 %, at the same 1.4 % false-alarm rate.

**The model (N API calls).** `--anchors N` queues N rows that have representative labels (a random sample or the whole column) to be asked again by the active version: the next N, in the seed's order, that this series of checks has not asked yet, and once all have been asked, those asked longest ago (#35). The re-asks are *probes*: they are recorded in the ledger but never change what the view shows. Each anchor's stored answer is then compared with its newest re-ask on the identical input; rows whose text changed since are skipped. Without a change, the old and new answers are two draws from the same model, so either is equally likely to be the right one:
- **Accuracy.** An exact one-sided sign test on the anchors where exactly one of the two answers is right. The alarm means the model now answers worse.
- **Scores.** An exact two-sided sign test on whether each anchor's score went up or down. The alarm means the model behaves differently, even if accuracy has held, so the calibration may be stale.
- **The evidence (#35).** Every labelled row's first re-ask in the series counts once, at whichever check it came. Such comparisons are independent of one another, and where the two answers differ, each sign is a fair coin without a change. So betting on the signs, worse against better and scores down against up, with a mixture of betting martingales (Waudby-Smith & Ramdas, 2024), gives evidence that piles up over the series and may be read whenever you like. A fixed panel couldn't do this: every check compared the same rows' re-asks with the same stored answers.
- **Rows without labels (#36).** `--probes M` also re-asks M rows that have no random or whole-column label, in the same kind of rotation, those judged before the series began first. They can't show whether an answer got worse, but their scores can show that the model changed: they join each check's scores test, and each one's first re-ask adds to the scores evidence. So the scores evidence keeps growing after every labelled row has been asked. Accuracy stays with the anchors.
- **Rows asked twice (#37, #38).** One re-ask can't show a change in no one direction: the same model asked again also answers differently now and then. With `--twice`, each of the `--probes` rows is asked twice, in separate requests. Without a change, its stored answer and the two new ones are three draws from the same model, so when exactly two of them agree, the odd one is the stored answer a third of the time. After a change it is far more often, whichever way the answers moved. Evidence that never expires bets on that, from each row's first pair of re-asks in the series. `--twice` needs one row per request: with several, a row's two new answers would share their companions in the request, and agree more than two draws of the model do.

**Repeated checks.** Drift checks are meant to run on a schedule, and every run is another chance of a false alarm. Over the whole series the chance of any false alarm stays at most 5 %:
- **The window tests** of the k-th check spend 0.04 · 6 / (π² k²): 0.024 for the first, 0.0061 for the second, 0.0027 for the third. Those budgets sum to 0.04 (a union bound), split equally between answers and scores.
- **The evidence** gets the other 0.01 once, for the whole series, however often it is read.
- **The model check's sign tests** spend 0.035 · 6 / (π² k²) at its k-th check, 0.021 for the first. **Its evidence** gets the other 0.015 once (#35): half for accuracy, and the half that needs no labels split between scores and rows asked twice (#38), so each of those two needs an e-value of 267.

Recalibrating, the usual answer to an alarm, starts a new series with a new cut; so does promoting a new version.

**How the budget is split (#34).** #33 split the budget in half without trying other splits. `jevbench driftsplit` gave the window test shares of 0.5 to 1 on the same simulated draws: the four tasks' injected first checks, and series of 12 checks of 100 rows and 52 of 30, with shifts from the first check and from halfway ([report](../../results/drift-split/report.md)). A rule fixed before the first run chose the share: the smallest worst-case shortfall, in points, from the best share in any scenario. At 0.8 the worst shortfall was 5.5 points, against 15.8 at 0.5, and 91 with no evidence at all.
- **What it gains.** A single first check catches up to 11 points more than with half and half, for example 31 % → 42 % for a 15-point shift in 100 Kubernetes feature requests. The first check after a change within a series catches up to 7 more.
- **What it costs.** The evidence needs a little more to reach its threshold. Over 52 weekly checks of 30 rows, a lasting shift was caught up to 5.5 points less often by the end: 81.5 % → 76 % for 15 points on the 500 complaints.
- **False alarms** stayed at most 4.5 % over a whole series, and 1.8 % at a first check.
- **Not used to choose:** the real Kubernetes feature-request shift (p = 0.0081) is caught again at a first check, which now needs p < 0.012.

**How the model check got its evidence (#35).** `jevbench driftmodel` compared five designs on real re-asks ([report](../../results/drift-model/report.md)). The data were the 300 labelled Kubernetes issues (a stored answer and two real re-asks; jev-1.14, and the options without their descriptions, as real changes) and the 2,000 labelled CFPB complaints of Phase 0 (a stored run and three repeats; shuffled batches, and one row per request, as real changes). Each run made 12 checks of 50 or 100 anchors; 500 runs per scenario. The rule of #34 chose the rotating panel with evidence, its sign tests on 70 % of the budget.
- **The fixed panel stopped learning.** A change it missed at the first check, it missed at the twelfth: 24 % for the options without their descriptions, at both. The rotating panel's evidence caught it by the twelfth every time.
- **A change halfway through:** a 5-point accuracy drop from check 7 on the complaints was caught by check 12 in 81 % of runs, against 2 %.
- **Conformal test martingales failed** (Vovk, 2021). Betting on each anchor's conformal p-values among its own asks caught at most 10 % of anything. The model answers mostly the same each time, so the p-values are randomised over ties and the bets mostly pay noise.
- **The price:** the first check after a change catches up to 5 points less (77 % against 82 % for one row per request from check 7).
- **False alarms:** at most 0.6 % of runs over 12 checks, whatever the design.
- **Labels are the limit.** Each labelled row counts once per series. With 300 labels and 50 anchors a check, the evidence stops growing after six checks: a change from check 7 on the Kubernetes issues was caught by check 12 in 8 % of runs (3 % with the fixed panel).

**Rows without labels (#36).** `jevbench driftprobes` gave the same checks probes on top of their anchors, with the rest of each table as rows without labels ([report](../../results/drift-probes/report.md)): 300 of the 2,000 complaints labelled, 50 anchors a check over 18 checks; and 100 of the 300 Kubernetes issues labelled, 25 anchors a check over 12. 500 runs per scenario.
- **Added to the anchors, probes only helped.** One row per request on half the complaints, beginning at check 7 when the labels had run out, was caught by check 18 in 6 % of runs with the anchors alone. With 50, 100 or 200 probes a check: 63 %, 99 % and 99.6 %. On a quarter of the complaints from the first check: 7 % against 56–94 %. The options without their descriptions, beginning after the Kubernetes labels had run out, went from 1 % to 10–63 % by check 12. Nowhere did probes lose more than 2.2 points, within the simulation's noise; false alarms stayed at most 1.4 %.
- **Swapping anchors for probes failed the test fixed beforehand.** At the same calls, 50 anchors and 50 probes caught score changes after the labels had run out more often than 100 anchors, but accuracy drops far less often. A 10-point drop was caught at the first check in 22 % of runs against 68 %, and from check 7 by check 18 in 2 % against 44 %. Only labels show accuracy, so probes are added calls, not a swap.
- **Some changes leave no trace without labels.** Shuffling the complaints into new batches changed 16 % of answers, but neither their scores nor any answer's share moved in one direction. No design caught it; rows asked twice do (#37, #38).

```
$ pgdocket drift tickets --anchors 60 --probes 100 --run        # the third check of a series, 150 labelled rows
…
model: 60 labelled rows and 100 without labels asked again, 0.0 days after their stored answer (median) · check 3
  accuracy 100.0 % → 100.0 %: worse on 0, better on 0, p = 1 (needs p < 0.0012) · evidence from 150 first re-asks: 1 (needs 133)
  scores: up on 0, down on 0, mean change +0.000, p = 1 (needs p < 0.0012) · evidence from 300 first re-asks: 1 (needs 267)
  every labelled row has been asked in this series, so its accuracy evidence has stopped growing: label more rows at random
  → no change in the model detected
```

**Asking rows twice (#37, #38).** Shuffled batches left no trace because the model check compares a stored answer with one new answer, and one difference says nothing without a direction: the same model asked again also answers differently now and then. Two new answers do say something. If nothing changed, the stored answer and the two new ones are three draws from the same model, so when exactly two of them agree, the odd one is the stored answer a third of the time. After a change the two new answers agree with each other, and the stored one is the odd one far more often, whichever way the answers moved. `jevbench drifttwice` tested this on the same real asks ([report](../../results/drift-twice/report.md)), with evidence that bets on the stored answer being the odd one, against one chance in three. Each change was run once, so for rows asked twice the changed run stood for the stored answers, and two runs of the original for the new ones. 1,000 series per scenario; the criteria were fixed in the issue before any data was read.
- **A real change shows plainly.** With the shuffled-batches run in the stored answers' place, the first answer was the odd one on 82–87 % of the complaints that had one (about 350 of the 2,000). With one row per request: 78–84 %. With the options without their descriptions: 77–83 %, of about 13 of the 300 issues. jev-1.14 changed hardly any answer (about 4 issues had an odd one), so this test does not see it.
- **It caught what no other check does.** At the same calls, 50 rows asked twice a check caught shuffled batches by check 18 in every series; 100 rows asked once, in 2.4 %. An injected 10-point accuracy drop that began after the labels had run out was also caught in every series, against 2 %: answers changed, and that shows without labels.
- **At the same calls it is slower for changes that have a direction.** One row per request from check 7 was caught at that check in 78 % of series, against 98 % for 100 rows asked once. In nine cases it lost more than 5 points, so it is an option beside `--probes`, not a replacement.
- **Each row counts once.** A row's later pairs share its stored answer, so only its first pair is a fresh bet. The 200 issues without labels were used up after four to eight checks.
- **Series without a change stayed quiet:** at most 1.5 % of them alarmed, against 1.3 % without the new test.
- **The check on real repeats was not met as written in #37.** The first criterion: without a change, an exact test of "the stored answer is the odd one" against a third must not reject at 5 % on real repeats. The complaints have three repeats, so three ways to pair them. The stored run was the odd one on 34 of 95 rows, 43 of 93 and 31 of 90; the middle one rejects (p = 0.011), and the smallest of three such p-values is that small in 3 % of random relabellings of the runs. Everything else says chance: pooled it is 39 % (15 % of relabellings are as far from a third), the same triples' scores give 33.5–33.7 % of some 1,280 rows, and the Kubernetes re-asks 1 of 5. But the criterion did not say how to treat three pairings, so the strict reading decides.
- **Fresh runs settled it (#38).** Twelve fresh runs on 13 November ($0.36), with one test fixed in the issue before any was made ([report](../../results/drift-twice-repeats/report.md)). Each October run stood for stored answers, followed by its own pair of fresh runs in the same configuration: four in the original batches, one in shuffled batches, one with one row per request. Every fresh run disagreed with its October run on 1.6–3.4 % of complaints, so October's batches were reproduced. Pooled over the six triples, the stored answer was the odd one on 165 of 486 rows, 34.0 % (p = 0.77 against a third). Per triple it ranged from 24.5 to 42.2 %, and none rejected.
- **A month left no mark.** The fresh runs disagreed with October's as often as with each other: 2.9 % and 2.9 % in the original batches, 1.6 % and 1.3 % with one row per request. On scores, the stored one was set apart on 33.5 % of 7,046 rows.
- **The changes, forward.** The fresh runs also let the changes run the right way round: an October run in the original batches as the stored answers, then two fresh runs in shuffled batches, or with one row per request. The stored answer was the odd one on 81.3 % and 92.0 % of the rows that had one. #37's simulation, with the roles swapped, had assumed 82–87 % and 78–84 %.
- **So `--twice` is built, as #37 chose.** Half of the evidence budget that needs no labels goes to rows asked twice, in every series, so a series that never asks twice pays for it too: its scores evidence needs 267 instead of 133. In #37's simulation that cost series asked once at most 3.4 points. The same test on scores caught jev-1.14 in place of jev-1.13 by check 12 in 99.8 % of series, a change no other check has seen. It is not built: #37 fixed it as a comparison, to be built only if the answers test failed.

On the integration test's toy data, the second check of a series, after the model began to swap sales and billing (the fake model's scores never move):

```
$ pgdocket drift tickets --anchors 30 --probes 60 --twice --run
30 labelled rows queued to be asked again
60 rows without labels queued to be asked twice
…
model: 30 labelled rows and 60 without labels asked again, 0.0 days after their stored answer (median) · check 2
  accuracy 100.0 % → 23.3 %: worse on 23, better on 0, p = 1.2e-07 (needs p < 0.0027) · evidence from 60 first re-asks since 2026-11-13: 5.4e+05 (needs 133)  ⚠ dropped
  scores: up on 0, down on 0, mean change +0.000, p = 1 (needs p < 0.0027) · evidence from 180 first re-asks: 1 (needs 267)
  answers: of 120 rows asked twice since 2026-11-13, 50 had an odd one out, the stored answer on 50 (a third expected) · evidence: 8.4e+21 (needs 267)  ⚠ changed
  → the model answers worse than when it was calibrated: compare a new version, or pin another model
```

## Evidence

No API calls: `jevbench driftvalidate` replays the checks on stored Jev answers ([report](../../results/drift-validation/report.md)).

**False alarms stayed under 5 %.**
- **Rows, one check.** 1,000 random splits of each task's judged rows into a recent window and the rest gave 0.9–2.4 % false alarms across four tasks (500 to 2,000 rows) and windows of 50–400 rows. The check as it was gave 1.2–3.1 %.
- **Rows, a series.** 12 weekly checks of 100 new rows, or 52 of 30, from an unchanging population: 0.5–4.5 % of 200 runs alarmed at least once, against 1–4.5 % for the check as it was.
- **Model.** Real re-asks of the same model on the 300 labelled Kubernetes issues, two days after their stored answers: 0.0–0.6 % of anchor panels alarmed, at every panel size. All 300 together: 2 worse / 1 better, scores up 33 / down 33.
- **Simulation (in CI).** On 1,000 simulated decisions, 20 weekly window tests of an unchanging population, on the whole budget as before #33, alarmed at least once for 4.2 %, and a year of monthly model checks for 1.9 %. The evidence, read after each of 40 days of 1,000 unchanging streams, never reached its threshold of 1/α = 20.

**Real shifts were caught.** The most recent rows of each sample, against everything before (the check never sees the labels):

| task | rows: earlier → recent | what the check saw | true shift in the labels | alarm |
| --- | --- | --- | ---: | --- |
| CFPB complaints, 2,000 (last 365 days) | 1,073 → 927 | *credit reporting* 51.6 % → 62.8 % | 13.5 % | yes |
| CFPB complaints, 500 (last 365 days) | 269 → 231 | *debt collection* 15.6 % → 6.1 % | 15.7 % | yes |
| Kubernetes, feature request? (last 90 days) | 442 → 58 | *yes* 24.0 % → 8.6 % (p = 0.0081) | 17.2 % | yes: the window test needs p < 0.012 (with #33's half of the budget, 0.0076: no) |
| Kubernetes, which SIG? (last 90 days) | 440 → 60 | *scheduling* 10 % → 20 % (p = 0.085) | 14.2 % | no: 60 rows were too few |

**How big a shift one check catches.** Half of a task's rows are the reference; the window is drawn from the other half with one true answer made δ more frequent. Detection rate at a first check, 9-option CFPB product question, as it is (as it was):

| recent window | δ = 5 pp | 10 pp | 15 pp | 20 pp |
| ---: | ---: | ---: | ---: | ---: |
| 100 rows | 2.4 % (3.2 %) | 9.0 % (10.6 %) | 28.8 % (31.0 %) | 57.2 % (62.0 %) |
| 200 rows | 2.6 % (3.0 %) | 18.4 % (20.8 %) | 54.2 % (57.4 %) | 88.0 % (89.4 %) |
| 400 rows | 2.6 % (2.8 %) | 30.1 % (33.3 %) | 84.0 % (85.5 %) | 100.0 % (100.0 %) |

One check alone catches a little less than before #33: its window test has 80 % of the budget. Yes/no decisions are easier: on Kubernetes feature requests, a 100-row window caught a 20-point shift 87 % of the time (90 % as it was).

**What checks catch over time (#33).** The reference was 1,000 rows, and each check's new rows were drawn from the same task's rows, 200 runs each. From a given check on, one true answer was made δ more frequent. Share of runs with an alarm by the end, as it is (as it was):

| task | weekly checks | 15-point shift from the first | 20-point shift from halfway |
| --- | --- | ---: | ---: |
| CFPB, 2,000, which product? | 12 of 100 rows | 73.0 % (54.0 %) | 70.5 % (63.5 %) |
| CFPB, 2,000, which product? | 52 of 30 rows | 58.5 % (10.5 %) | 67.5 % (3.0 %) |
| CFPB, 500, which product? | 52 of 30 rows | 72.5 % (14.5 %) | 89.5 % (3.5 %) |
| Kubernetes, which SIG? | 12 of 100 rows | 94.5 % (75.0 %) | 96.0 % (84.5 %) |
| Kubernetes, which SIG? | 52 of 30 rows | 96.5 % (41.0 %) | 99.5 % (23.5 %) |
| Kubernetes, feature request? | 52 of 30 rows | 98.0 % (57.5 %) | 100.0 % (32.0 %) |

- **The check as it was went nearly blind after half a year of weekly checks.** Its budget had shrunk to under 0.0001, and its reference had absorbed the shift. A 20-point shift arriving in week 27 was caught by week 52 in 3–32 % of runs; now 67.5–100 %.
- **The price is the first look.** At the first check after a change the window test has 80 % of the budget it had, so it alarmed 3.5–7.5 points less often (9–15 with #33's half). A 20-point shift at week 7 of 12 was caught at once in 26–59.5 % of runs, against 32–67 %.
- **Four designs were tried in #33** (on the 2,000 complaints and the Kubernetes SIGs):
  - **The evidence alone** caught little at a first check (0–3 % for a 15-point shift), and needed many checks to pile up.
  - **The window test alone, against the fixed reference,** was best for a shift that begins at the first check (93 % for 15 points by check 12, at 200 rows a check), but missed late shifts in long series of small windows (1 % for 20 points from halfway, 60 checks of 30 rows).
  - **Betting against a band around the reference's share** (two-sample testing by betting, Shekhar & Ramdas), in place of universal inference: valid, and close, but behind it everywhere. For example 93 % against 97 % for a 15-point shift over 52 weekly checks of 30 rows on Kubernetes SIGs, and 67 % against 75 % for a 20-point shift from halfway on 2,000 complaints. Its bets also depend on the order rows arrive in; the counts universal inference uses don't.
  - **The window test and universal inference, half the budget each:** never far behind the best, and far ahead where the others fail. #34 then moved the split to 80 / 20 (see How the budget is split).

**Model changes.**

| new answers on the 300 issues | accuracy | worse / better | scores up / down | alarm | first-check alarm rate, 100 anchors (as it was) |
| --- | --- | --- | --- | --- | ---: |
| the same model, asked again | 91.3 % → 91.0 % | 2 / 1 | 33 / 33 | no | 0.1 % (0.2 %) |
| jev-1.14 in place of jev-1.13 | 91.3 % → 91.7 % | 1 / 2 | 43 / 50 | no | 0.2 % (0.3 %) |
| the options without their descriptions | 91.3 % → 89.3 % | 8 / 2 | 44 / 103 | scores moved | 59.1 % (64.9 %) |

- **The jev-latest switch was invisible,** as `compare` had found: 1.14 answers these issues like 1.13.
- **Removing the descriptions changed how sure the model is.** Accuracy fell 2 points, too few rows to prove it, but the scores moved on 147 of 300 issues (p = 1.3 × 10⁻⁶). With 200 anchors the check caught it on 99.8 % of panels.
- **A real accuracy drop needs about seven anchors' worth.** On the first check, the sign test needs about 7 more anchors answered worse than better (6 to 0 gives p = 0.016, above the test's 0.011). That is a drop of 7 points on 100 anchors, or 3.5 points on 200. Making the real re-asks wrong on 5 % of the anchors was caught on 7 % of 100-anchor panels, and on all 200-anchor panels.

## Limits

- **Small windows catch only large shifts.** With 100 recent rows, a 10-point shift in one answer usually goes unnoticed. Choose `--days` so that the recent window holds a few hundred rows.
- **The window test's budget still shrinks with every check, the evidence's doesn't (#33).** The evidence carries a lasting shift however often the check runs, but it is slow to show a fresh one: it pays for not knowing when a change began, and for the reference's own sampling error. So a shift is usually first seen by the window test, and a long series relies on the evidence.
- **The first look after a change is a little weaker than before #33.** The window test has 80 % of the budget it had, so a first check caught up to 7 points less than the check as it was. #33's half and half lost up to 16, and just missed the real Kubernetes feature-request shift.
- **The model check's accuracy evidence needs labelled rows it hasn't asked yet.** Each counts once per series (#35). Once every labelled row has been asked, the accuracy evidence stops growing, and the check says so: with 300 labels and 50 anchors a check, that is after six checks. Label more rows at random, or ask fewer each time, to keep it growing. `--probes M` keeps the scores evidence growing on rows without labels (#36), but only labels can show that answers got worse.
- **Changes in no one direction need rows asked twice.** A change that gives some rows another answer, without moving accuracy on the labelled rows or the scores one way, passes the other tests: shuffled batches changed 16 % of the complaints' answers. `--twice` catches it (#38), at two calls a row, and only with one row per request. Each row counts once per series, so 200 rows without labels, asked 25 at a time, last eight checks.
- **Keep `--days` while a series runs.** Its first check sets the evidence's blocks; later checks keep them, and change only the window.
- **Run it on a schedule, not when something looks odd.** The window tests' budgets, and the model check's sign tests', assume each check's timing does not depend on the data it tests. The evidence may be read whenever you like.
- **The rows check sees only the model's outputs.** A change in the inputs that leaves answers and scores alone is invisible to it, and a shift in answers is not proof that accuracy fell. Only labels can show that: after an alarm, label a fresh random sample of recent rows, or keep a running [audit](audit.md) of the accepted rows.
- **Anchors are labelled rows.** They show whether the model changed on those rows, not on every kind of row; rotating through them covers more of the labelled rows over a series. Labels drawn at random keep them representative. Use more anchors when answers are rare.
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
- Waudby-Smith & Ramdas. *Estimating means of bounded random variables by betting.* JRSS Series B, 2024. [arXiv:2010.09686](https://arxiv.org/abs/2010.09686)
- Vovk. *Testing Randomness Online.* Statistical Science, 2021. [doi:10.1214/20-sts817](https://doi.org/10.1214/20-sts817)
- Vovk, Petej, Nouretdinov, Ahlberg, Carlsson & Gammerman. *Retrain or not retrain: Conformal test martingales for change-point detection.* 2021. [arXiv:2102.10439](https://arxiv.org/abs/2102.10439)
- Zhang, Cai, Yu & Simeone. *Prediction-Powered Risk Monitoring of Deployed Models for Detecting Harmful Distribution Shifts.* ICML 2026. [arXiv:2602.02229](https://arxiv.org/abs/2602.02229)
