# Changelog

Notable changes, newest first. Dates are UTC; `#n` refers to issues in this repository.

## 2026-11-13

### Changed (`--harden`'s check: keep-one, #43)
- **`calibrate --harden` also judges each piece of a row alone, for choice decisions.** Each piece votes for its answer, weighted by that answer's probability, and the row goes to review when another answer outweighs its own (keep-one, the vote of structured ablation: Levine & Feizi, 2020). A payload written twice survives every erasure, so erase-and-check alone passed it; the pieces without it usually outvote it.
- **Chosen by `jevbench steertwice`** (new; real Jev calls on 10,003 new texts, $0.371, reusing #24's answers), by a rule fixed in #43 before any call. Five checks ran under `--untrusted` on the 300 Kubernetes issues: erasing each piece (as before), also each pair, also each group of near-duplicates, keeping each piece alone, and erasing or keeping. The attacks were #24's seven, and two written against the new checks: the claim twice with the second copy reworded, and the claim three times. A check flagging more than 9.1 % of the clean accepted issues was out (erasing pairs: 15.9 %), and the smallest worst case won.
- **What changes.** The worst attack left through falls from 15.0 % of issues to 7.0 % (the reworded copy), and the claim written twice from 7.3 % to 3.0 %; three copies still got 5.3 % through. The check sends 8.5 % of clean accepted issues to review instead of 4.1 %, and takes about ten calls per accepted row instead of five. 82.3 % of issues stay `auto` instead of 86.3 %, 94.3 % of them right.
- **Yes/no decisions are only erased.** Keep-one was measured on a choice task, and in a yes/no question every piece without the evidence votes "no".
- **Upgrading.** Rows that passed erasing alone are checked again the next time you run `calibrate --harden`. `docket.robust.answers` now records how each answer was asked (`how`: `erase` or `keep`) and for which piece. Rows of one piece are flagged without any call.
- **`check --steer`** runs the same check, and reports what it caught and flagged for nothing, and erasing alone as before. Like the worker, it now counts rows of one piece as flagged. It estimates up to about 40 calls per row (22 for yes/no decisions).

### Changed (the audit's bets, #42)
- **`pgdocket audit` sizes its bets from the last 20 audited rows** (40 before). After a long good stretch, the 40-row window still held the good rows when a fall began, so the test waited before betting.
- **Chosen by `jevbench auditbets`** (new, no API calls), by a rule fixed in #42 before the first run. The candidates were windows of 20, 40 and 80 rows, a larger cap on each bet, fresh starts (a mixture of tests started at every batch, weighted 1 / (b (b + 1))), and half of each. The scenarios were simulated falls and falls injected into the real CFPB stream. The 20-row window's largest shortfall from the best design was 5.9 points, against 15.1 for 40 rows and 12.8 for fresh starts, whose weight over start times cost about what starting fresh saved.
- **What changes.** A fall to 70 % on the CFPB stream is caught within 40 audited rows in 17 % of audits (4 % before), and the median wait from the change to the alarm drops to 65 audited rows (72). Small falls watched for long lose up to about 6 points, and a fall with no good stretch before it takes a few more labels (109 against 101 for 10 points at a 90 % target). False alarms stay at most 4.0 % at exactly the target, and none on the real streams.

### Examined, not built (audit labels from the model's scores, #41)
- **The question:** could the model's score on every accepted row cut the labels `pgdocket audit` needs? Prediction-powered methods would use it as a control variate (PPI, PPI++, prediction-powered e-values, prediction-powered risk monitoring). At best that removes the share corr(score, right)² of one label's variance over the accepted rows, so 1 / (1 − corr²) bounds the saving of any such design.
- **Measured** (`jevbench auditscores`, new, no API calls). The bound was 1.10× on the live Kubernetes decision and 1.00× on the CFPB stream the audit was validated on (85 %); at 80 and 75 % on the same complaints, 1.03× and 1.14×. Jev's probabilities pile up at the top: 71–74 % of the accepted rows share a single calibrated confidence, and the correlation with being right is 0.04–0.35. Drawing the least-sure rows more often would have saved at most 23 %. Nothing was built.

### Checked (jev-1.14 run forward, #40)
- **jev-1.14 asked twice more** on the 300 Kubernetes issues, through the worker as `--twice` does: 600 asks, each second ask linked to its first, none failed, $0.026. #39's numbers for it had the roles swapped, since it had been asked once.
- **The forward triples** (checks fixed in #40 before the asks). The stored jev-1.13 score was the odd one on 63 of 85 issues (74.1 %, p = 2e-14 against a third; 67–75 % with the roles swapped), and the stored answer on 4 of 4. Against its own ask of 22 October, jev-1.14 gave 23 of 66 (34.8 %, p = 0.79), so it had not changed itself.
- **The simulation forward** (`jevbench twicescores --forward`). The two bets caught jev-1.14 from the first check by check 12 in every series (99.9 % with the roles swapped), on half the issues in 49 % (38 %), and the answers alone in under 1 %. #39's rule chose the two bets again, so nothing was rebuilt.

### Added (the scores of rows asked twice, #39)
- **A second bet on the pairs of `--twice`, by scores.** Of a row's stored score and its two new ones, the two closest make the pair and the third is the odd one; without a change it is the stored one a third of the time. The model check now bets on that beside the answers, the two splitting the share of the evidence budget that rows asked twice have, so each needs an e-value of 533 instead of 267. No other threshold moved. It catches changes that move scores and hardly any answer, in no one direction, which no other check saw: jev-1.14 in place of jev-1.13, which `jev-latest` moved to on 14 October.
- **Chosen by `jevbench twicescores`** (new, no API calls) among five readings, by a rule fixed in #39 before the first run: by answers alone (as in #38), by scores alone, both as two bets, answers then scores as one bet, and every option's probability (total variation). Data: the twelve CFPB runs in the original batches as stored and re-asks, the changes run forward from three runs each, the Kubernetes changes with the roles swapped; 1,000 series per scenario.
  - **Two bets won.** Their worst shortfall from the best reading in any scenario was 28.9 points, against 94–100 for the others. jev-1.14 from the first check was caught by check 12 in 99.9 % of series with 25 issues asked twice a check, against 0.4 % by answers alone.
  - **One merged bet lost answer changes.** Most rows' scores differ between asks, and at chance they drowned the few whose answers changed: a 10-point accuracy drop after the labels had run out was caught in 70–71 % of series, against 100 %.
  - **Without a change,** every reading alarmed in at most 1.6 % of series. On #38's real repeats, each gave the stored one as the odd one on 33.4–34.0 % of rows.

### Added (asking rows twice, #38)
- **`pgdocket drift --anchors N --probes M --twice`** asks each of the M rows without labels twice, in separate requests (migration 015: a `twice` flag on the queue, and `pair_of` linking a second ask to the first). Without a change, when exactly two of a row's three answers agree, the odd one is the stored answer a third of the time. Evidence that never expires bets on it being more often, from each row's first pair of re-asks in a series. That catches changes in no one direction, which no other check sees. `--twice` needs one row per request, and refuses a decision that sends more.
- **The budget.** As #37 chose, half of the model check's evidence budget that needs no labels goes to rows asked twice, in every series. The scores evidence now needs 267 instead of 133; in #37's simulation that cost series asked once at most 3.4 points. Accuracy's evidence and the sign tests are unchanged.
- **Settled on fresh runs** (`jevbench twicerepeat`, new, no API calls). Twelve fresh runs on the 2,000 complaints, $0.36, with the test fixed in #38 before any was made. Each October run stood for stored answers, followed by its own pair of fresh runs in the same configuration. Pooled over the six triples, the stored answer was the odd one on 165 of 486 rows (34.0 %, p = 0.77 against a third), per triple 24.5–42.2 %, none rejecting. The fresh runs disagreed with October's as often as with each other, so a month left no mark. With the changes run forward, the stored answer was the odd one on 81.3 % (shuffled batches) and 92.0 % (one row per request) of the rows that had one, against the 82–87 % and 78–84 % #37's simulation had assumed with the roles swapped.
- **What #37 measured** (1,000 simulated series per scenario): at the same calls, 50 rows asked twice a check caught shuffled batches by check 18 in every series, against 2.4 % for 100 rows asked once. Changes with a direction show later (78 % against 98 % at the first check after one row per request), so `--twice` is an option beside `--probes`, not the default.

## 2026-11-12

### Tested, not built (asking rows twice, #37)
- **The idea.** Ask a row without a label twice. Of its stored answer and the two new ones, when exactly two agree, the odd one is the stored answer a third of the time if nothing changed, and far more often after a change, whichever way the answers moved. That would catch changes in no one direction, such as shuffled batches (16 % of answers changed, #36), which no drift check sees.
- **`jevbench drifttwice`** (new, no API calls, 1,000 series per scenario) tested it on real asks, with the criteria fixed in the issue beforehand. Where a real change had altered answers (shuffled batches, one row per request, the options without their descriptions), the first of the three asks was the odd one on 77–87 % of the rows that had one. At the same calls, 50 rows asked twice a check caught shuffled batches by check 18 in every series, against 2.4 % for 100 rows asked once; a 10-point accuracy drop after the labels had run out, in every series against 2 %. It was slower for changes with a direction (78 % against 98 % at the first check after one row per request), so it would be an option, not a replacement. Series without a change alarmed in at most 1.5 %.
- **Not built: the no-change check on real repeats was not met as written.** Of the three ways to pair the complaints' three repeats, one rejected "the stored answer is the odd one a third of the time" (43 of 93 rows, p = 0.011; the smallest of three is that small in 3 % of random relabellings). Pooled it is 39 % (15 % of relabellings are as far off), and the scores of the same triples give 33.5–33.7 %, so it reads like chance. But the criterion did not say how to treat three pairings, and the strict reading decides. Fresh repeat runs, with one test fixed beforehand, would settle it.
- **Also measured:** the same test on scores caught jev-1.14 in place of jev-1.13 by check 12 in 99.8 % of series, a change no other check has seen. No pgdocket code changed.

## 2026-11-08

### Added (rows without labels for the model check, #36)
- **`pgdocket drift --anchors N --probes M`** also re-asks M rows that have no random or whole-column label, the next M in the seed's order that the series hasn't asked yet, rows judged before the series began first (migration 014, with a partial index on re-asks). Their scores join each check's scores test, and each one's first re-ask in the series adds to the scores evidence, which then outlasts the labelled rows. Accuracy stays with the anchors. Once every labelled row has been asked in a series, the check says so.
- **Why scores only:** on the real changes at hand, rows without labels showed a change through their scores, not through which answer they gave. One row per request: scores up on 1,161 complaints and down on 379, p = 3e-92; the strongest answer drift, p = 3e-5 before correcting for nine answers. The options without their descriptions: scores p = 1.3e-6, answers p = 0.12.
- **Validation** (`jevbench driftprobes`, new, no API calls, 500 runs per scenario). With 300 of the 2,000 complaints labelled and 50 anchors a check over 18 checks, a change in scores on half the complaints from check 7, after the labels had run out, was caught by check 18 in 6 % of runs with the anchors alone, and in 63 %, 99 % and 99.6 % with 50, 100 or 200 probes. Probes never lost more than 2.2 points anywhere, within simulation noise; false alarms stayed at most 1.4 %.
- **The test fixed beforehand failed, so probes are added calls, not a swap.** At the same calls, 50 anchors and 50 probes caught score changes more often than 100 anchors, but a 10-point accuracy drop at the first check in 22 % of runs against 68 %. Shuffled batches, which changed 16 % of answers in no one direction, stayed invisible to every design.

## 2026-11-06

### Changed (model checks that keep collecting evidence, #35)
- **The model check's anchors rotate** (migration 013). Each `pgdocket drift --anchors N` re-asks the next N labelled rows, in the seed's order, that the series hasn't asked yet; once all have been asked, those asked longest ago. A fixed panel compared the same rows' re-asks with the same stored answers at every check: a mostly deterministic model told it little new, and its e-values couldn't be multiplied.
- **Evidence that never expires.** Every labelled row's first re-ask in a series counts once. Its stored answer and that re-ask are exchangeable and independent of every other row's, so where they differ each sign is a fair coin. A mixture of betting martingales on the signs, worse against better and scores down against up, gets 30 % of the model check's budget once. The sign tests on each check's re-asks keep 70 %, spent over the checks (`drift.MODEL_SHARE`). The output shows the evidence and what it needs.
- **Chosen by `jevbench driftmodel`** (new, no API calls), by #34's rule fixed beforehand, on real re-asks: 300 labelled Kubernetes issues and 2,000 labelled CFPB complaints, with real model changes and injected accuracy drops, 500 runs per scenario. Over 12 checks:
  - **A fixed panel stopped learning.** A change it missed at the first check, it missed at the twelfth (24 % for the options without their descriptions). The rotating panel's evidence caught it by the twelfth every time.
  - **A 5-point accuracy drop from check 7** on the complaints was caught by check 12 in 81 % of runs, against 2 %.
  - **Conformal test martingales** (Vovk) on a fixed panel caught at most 10 % of anything: a mostly deterministic model's asks tie, so the p-values are randomised and the bets mostly pay noise. Set aside.
  - **The price.** The first check after a change catches up to 5 points less. False alarms stayed at most 0.6 %. With 300 labels and 50 anchors a check, the evidence stops growing after six checks.
- **`jevbench driftvalidate`** now shows a first model check as it is and as it was. On the 300 issues, the options without their descriptions were caught on 59 % of 100-anchor panels (65 % before) and on 99.8 % of 200-anchor panels either way; the same model asked again alarmed on at most 0.5 %.

### Changed (the drift budget, rebalanced, #34)
- **The rows check's window test gets 80 % of the false-alarm budget, the evidence that never expires 20 %** (half each in #33). `jevbench driftsplit` (new) compared window shares of 0.5 to 1 on the same simulated draws: the four tasks' injected first checks, and series of 12 checks of 100 rows and 52 of 30, with shifts from the first check or from halfway. A rule fixed before the first run chose the share: the smallest worst-case shortfall from the best share in any scenario, 5.5 points at 0.8 against 15.8 at 0.5. A first check's window test now needs p < 0.012 (0.0076 in #33); answers and scores each get 0.005 of evidence budget (0.0125).
- **Validation** (`jevbench driftvalidate`, rerun on the same draws as #33):
  - **A single check** caught 0.2–10 points more than with half and half, and is 0–7 points behind the check as it was (up to 16 with #33). The real Kubernetes feature-request shift (p = 0.0081) is caught again.
  - **Lasting shifts.** Over 52 weekly checks of 30 rows, a 20-point shift from week 27 was caught by week 52 in 67.5–100 % of runs (74–100 % with #33, 3–32 % as it was), and 15 points from week 1 in 58.5–98 % (62.5–98 %).
  - **False alarms** were 0.9–2.4 % at a first check and 0.5–4.5 % over a series.
- **Tests.** CI checks `driftsplit`'s shortcut statistics against `drift.rows_check`, check by check, at both shares.

### Changed (drift checks that don't go blind, #33)
- **The rows check of `pgdocket drift` keeps evidence that never expires.** Every row since the series' first check adds to one e-process per answer, and per decile of the reference's scores. Each is universal inference (Wasserman, Ramdas & Balakrishnan, 2020): the likelihood that a change began in some window, prior 1 / (b (b + 1)) over the windows, the share before and after it Krichevsky–Trofimov mixtures, against the maximum likelihood with none. Its chance of ever reaching 1/α is at most α however often it is read, so a shift that lasts piles up evidence instead of meeting a smaller budget each time.
- **The window test compares the recent rows with the rows from before the series began,** the first check's cut, no longer with everything before the window. Once a shift had lasted, the shifted rows had become the reference and the alarms faded.
- **The budget is split.** The window tests spend 0.025 · 6 / (π² k²) at the k-th check; the evidence has the other 0.025 once. Either can raise the alarm, and the 5-point and KS 0.10 effect floors stay. The series' first check fixes the cut and the evidence's blocks (its `--days`). The model check keeps its budget: its checks compare re-asks with the same stored answers, so their e-values couldn't be multiplied.
- **Validation** (`jevbench driftvalidate`, no API calls, four tasks of stored Jev answers):
  - **False alarms.** Over 12 or 52 weekly checks of an unchanging population, 0.5–4 % of runs alarmed (1–4.5 % before).
  - **Lasting shifts.** Over 52 weekly checks of 30 rows, a 20-point shift arriving at week 27 was caught by week 52 in 74–100 % of runs, against 3–32 % before; a 15-point shift from the start in 62.5–98 %, against 10.5–57.5 %.
  - **The price.** One check alone catches less, its window test having half the budget: 9–15 points less at the first check after a change. The real Kubernetes feature-request shift (p = 0.0081) is now just missed at a first check.
  - **Tried and set aside:** the evidence alone (slow to show a fresh shift); the window test alone against the fixed reference (blind to late shifts in long series); betting against a band around the reference's share (Shekhar & Ramdas), close to universal inference but behind it everywhere, and dependent on the order rows arrive in.

### Added (betting intervals in SQL, #32)
- **`docket.estimate()` gives the betting interval itself** below the floor of 10 labelled rows with or without an answer (migration 012), from labels drawn at random only. A new column, `method`, says `normal` or `betting`; such rows are `reliable`. Before, SQL clients got the normal interval with `reliable = false`, and only the CLI replaced it.
- **`docket.betting_interval(y, f, model_total, rows, alpha)`** does the arithmetic of `pgdocket.betting.interval` in PL/pgSQL, so a role without superuser can install it. It finds each end of the interval by bisection, since the shares no bet rejects form an interval: the capital betting higher never grows as the candidate share rises, and the capital betting lower never shrinks.
- **The CLI prints what the function returns** and drops its own Python fallback. On a database still at migration 011 it says to run `pgdocket install`.
- **Validation** (`jevbench sqlbettingvalidate`, no API calls). In 303 random cases, scopes of 40 rows to a million and 1 to 1,200 labels, SQL and Python gave the same interval every time; 7 were empty both ways. It takes 15–90 ms for 1,500 to 10,000 labels. On the live `k8s_route` decision, `docket.estimate()` returns #30's table interval for interval, in 79 ms for ten answers. A CI test checks 62 cases.

### Added (rare answers in weighted shares and with several models, #31)
- **`estimate --weight`** gives a betting interval, marked `(betting)`, below its floors.
  - **Labels drawn by weight** (fewer than 5 drawn each way): `betting.sps`. Each label is scored by its chance of being drawn next, as in betting for weighted sampling without replacement (Shekhar, Xu, Lipton, Liang & Ramdas, 2023). Ranked by u / w, the row drawn at rank t is row i with probability in proportion to w / (1 − t·w), so each score carries the factor (1 − u).
  - **What a candidate share implies** needs Σ w / (1 − t·w) over the rows not yet drawn. `groups.drawn_rows` reads it in SQL: the run, the heaviest of the rest row by row, and 40 power sums.
  - **Labels drawn at random** (fewer than 10 each way counted by weight, or a heavy row unlabelled): `betting.weighted`, scaled by the heaviest row not yet drawn. It is valid, and wide while a heavy row is unlabelled; the table still points to drawing by weight.
- **`estimate --with`** below 10 labelled rows each way: #30's interval from the labels and the first version that judged every row (the active one, normally), fixed before any label is looked at. **`--with --weight`**: the labels drawn by weight and the active version.
- **The plans take betting where it needs fewer labels:** `--weight --target`, the active version's plan under `--with --weight`, and under `--with` the plan for the version betting uses.
- **Validation** (`jevbench rarevalidate`, no API calls), on the CFPB join of #26 and the CFPB regions of #28, with Jev and Laya:
  - **Coverage, labels drawn by weight.** Below the floor, the usual interval held 63–93 %; betting held 96.2–99.8 % in all 36 cells; what pgdocket reports held 95.0 % or more.
  - **Labels drawn at random.** The flagged usual interval held 49–100 %; betting held every time, at ± 30–45 points.
  - **Labels, in paired rounds** (each run on the same draws both ways): with several models by region, 57–61 % fewer for student loans, 4–8 % for mortgages, none for credit cards. With `--with --weight`, 22–28 % fewer for debt collection and 12–17 % for credit cards. With `--weight` alone, 0–16 % fewer. In no run did betting take more labels.
  - Write-up: `docs/phase1/rare-answers.md`.

### Fixed (#31)
- **`--with` plans could ask more of betting than of the floor.** When a group's labels held no row with the answer, its betting estimate of 0 became the plan's share, and the floor asked for every row. The plan now takes the share from the usual estimate, never nearer 0 or 1 than 1 in (labels + 2). This was caught in validation, before release.
- **`betting.interval` checks once more, after the last label, that what is left is possible.** A share already impossible given every label seen is never reported.

### Added (rare answers: betting intervals below the floor, #30)
- **`pgdocket estimate`** gives a betting interval, marked `(betting)`, wherever the labels hold fewer than 10 rows with or without an answer. Before, such an answer got the normal interval marked ⚠, or none with no labelled example. **`estimate --by`** does the same group by group.
  - **The interval** (`pgdocket/betting.py`) is valid at any number of labels and wherever the labelling stops. It follows Waudby-Smith & Ramdas (2024) and PPI's nonasymptotic variant (Angelopoulos et al., 2023).
  - **How it bets.** Bets are on the rectifier y − λ·f, with λ fitted to the labels before each one. Each bet is against the share left among the rows not yet labelled. The bets are predictable plug-in confidence-sequence bets, hedged both ways, staking at most ¾ of the safe maximum.
  - **The model's share is known from every row,** so all of α goes to the labels.
  - **In the order drawn:** when imported, then by a hash of the key.
- **`--by --target`** plans each group below the floor on whichever reaches the target with fewer labels: the normal interval, which needs the floor, or betting, judged on a typical draw.
- **`docket.estimate()` in SQL is unchanged:** the normal interval with `reliable = false`. The weighted and several-model estimates keep their floors.
- **Validation** (`jevbench bettingvalidate`, no API calls): 500 Kubernetes issues and 2,000 CFPB complaints, each answered by Jev 1.13 and by Laya.
  - **Coverage.** The betting interval held 95.4–100 % in all 152 cells (500 draws of 50–300 labels each). The normal interval held 28–98 % of the draws below the floor (median 87 %) for answers under 8 % at up to 200 labels.
  - **Labels saved.** By region and year, each group to ± 5 points: 64–71 % for an answer on 1.8 % of complaints, 34–39 % on 5.5 %, 21–27 % on 7.1 %, 4–9 % on 12 %. At ± 3 points: 41–46 %, 6–14 %, 0–3 % and none. Final intervals held at least 97 %.
  - **Settings.** Bets tuned to a known number of labels were narrower but not valid over rounds. aGRAPA bets were up to 63 % wider.
  - Write-up: `docs/phase1/rare-answers.md`.

### Fixed
- **`--by --target` stopped short of its target.** Groups ended at ± 5.0–5.5 points for ± 5: the plan's formula left out the model's own spread on unlabelled rows. It also judged the floor from the estimated share, so a group with 8 of its 10 labelled rows could be told it had enough. In some cells only 8 % of runs reached every group. A group short of its target now asks for n · (half-width / target)², and at least a tenth more; below the floor, for the rows still missing on top of its labels. Runs now reach every target 99–100 % of the time.
- **A betting interval could reject the true share** when the labels held every row with the answer, since N · (k / N) − k is not exactly 0 in floating point. This was caught in testing, before release.

## 2026-11-04

### Added (a live run of #29 on the CFPB join)
- **`jevbench livejoin`** runs `estimate --with 2 --weight … --target --label-cost --queue --draw` round after round in Postgres, with real calls, capped by `--max-usd`.
  - **The setup.** The 2,000 CFPB texts and the 21,980 complaints that carry them are two tables, and each text is counted by a correlated count over the join. Laya judges every text as the active version, and Jev 1.14 one row per request is version 2.
  - **Labels.** Drawn texts are labelled with the product their complaint chose.
  - **Resuming.** Everything is in the ledger, so a second run picks up where the first stopped.
- **Result at ± 2 points**, for credit reporting, debt collection and credit card in turn:
  - **Every interval held the truth.** Jev cost $0.0102, on 443 of the 2,000 texts.
  - **Narrower intervals, but no labels saved.** Jev narrowed credit reporting's interval from ± 1.82 to ± 1.60 points and credit card's from ± 0.30 to ± 0.27. Laya alone's rounds on the same ranking take the same 107 and 386 labels.
  - **Why:** the heaviest texts are certain to be labelled, so 78–89 % of the weight was known exactly. The pilot and the floors set the counts.
  - **Laya's answers matched its stored Phase 0 answers on every text.**
  - Write-up: `docs/phase1/models.md#live-on-the-cfpb-join`.

### Fixed
- **`pgdocket label --csv`** reported a file from `estimate --weight --draw` as "manual". It now says "drawn by weight", as the ledger has always stored the rows (#26).

### Tested
- **Upgrade in place.** A ledger at migration 004, holding a decision with three versions, 91 labels and a calibration, upgrades to 011 with `pgdocket install`, and its view still reads.
- **Jev on every row.** The weighted Postgres test also queues V on every row (an infinite cut-off), as a plan for a small table does.

### Added (several models for a weighted share, #29)
- **`pgdocket estimate NAME --answer A --with V --weight EXPR [--by COL]`**: a weighted share from labels drawn by weight, the active version on every row and version V on a longer run of the same ranking by u / w.
  - **The estimate** is the two-phase regression estimator for samples drawn in proportion to weight (Särndal, Swensson & Wretman, 1992). Rows certain to be labelled count exactly. V's share comes from its run, regressed on the active version's answers, and the active version's share from every row.
  - **When V counts.** Only once the labelled rows hold 10 with and 10 without the answer, all inside V's run. Until then the estimate is #26's.
  - **Its variance** weights each phase's residuals by 1 − π, so nearly certain rows add little.
- **`--target --label-cost`** searches the two cut-offs of the ranking for the cheapest pair that reaches the target. The variance formula counts the rows that become certain. With no certain rows it is #27's plan in the population where a row appears as often as its weight.
  - **Pilots and rounds.** A pilot of 30 rows drawn by weight comes first, V judging them. Each round draws at most four times as many labels as it has, and the next round plans again.
  - **Also with `--by`:** this same plan and the `--queue` and `--draw` actions run in each group.
- **`--queue`** has V judge its run; **`--draw`** writes the labels' run, marked as drawn by weight.
- **Validation** (`jevbench multiweightvalidate`, no API calls): the CFPB join of 2,000 texts and the 21,980 complaints that carry them, to ± 2 points. The active version is batched Jev or Laya on every text; Jev one row per request takes a run of the ranking; labels cost $1.
  - **Coverage.** The intervals held 93.8–99.4 % (#26's: 95.6–99.4 %). Where Jev counted they were 9–20 % narrower.
  - **Cost.** For credit reporting the cheapest mix cost 12–19 % less than labels with the active version alone, with Jev on 17,000–37,000 texts. For the rare answers (1.6 % and 2.4 % of the complaints) the floor of labels sets the count, and it saved 0–6 %.
  - **In rounds** on a million texts, credit reporting cost 10–17 % less. The rare answers ranged from 7 % more to 4 % less. Final intervals held 91–96 %.
  - **Caught in testing:** the estimate without V had first been a two-phase form; it held 81–94 % in draws with few labels on the rarer side, so it is #26's now. A step to make V usable had first labelled as far as the active version alone needed, which wasted V; and a pilot that fell short asked for nothing more.
  - Write-up: `docs/phase1/models.md#weighted-shares`.

## 2026-11-03

### Added (several models by group, #28)
- **`pgdocket estimate NAME --answer A --with V --by COL`**: the share in each group of COL, from the group's own labels and each version's answers on its rows.
  - **Which labels count.** Labels drawn at random from the table, and labels drawn within COL's groups (`label --stratified COL`).
  - **The whole scope** is the groups' estimates weighted by their rows.
  - **Flags.** Groups where the active version's error differs from the others' are named, as with `--by`.
- **`--target --label-cost`** gives each group's cheapest mix of labels and rows judged by each version, and the total. Beside it are labels alone in every group and the active version on every row (`estimate --by`).
- **Pilots and stops**, with or without `--by`. With fewer than 30 labels, a group's plan is a pilot of 30, each version judging those rows. A group with no more rows than that is labelled whole. Once an interval is within the target, with 10 labels each way, nothing more is needed.
- **`--queue`** has each version judge what the cheapest mix asks: the first rows of each group's shared order, labelled rows first. **`--draw`** writes the next labels in each group in the same order, for `label --stratified COL`. So in every group the labels lie inside each version's sample.
- **Validation** (`jevbench multigroupvalidate`, no API calls): the 1,992 CFPB complaints from the four US Census regions, to ± 3 points in every region. The active version (batched Jev, or Laya) is on every row; Jev one row per request judges a sample in each region; labels cost $1.
  - **Coverage.** On 200,000 resampled complaints, each region's interval held 91–100 %, and the regions together 93–98 %. The intervals were 3–49 % narrower than with the active version alone.
  - **Cost.** On 100 million rows, drawing within regions cost 49–67 % less than one design drawn across the table. Adding Jev on a sample saved 11–24 % over Laya alone for three answers, and 3–10 % over batched Jev. Mortgage saved nothing: the floor of 10 labels each way sets the count.
  - **In rounds** (a pilot, then each region's plan until met) the cost was 4–21 % below the same rounds with the active version alone. Final intervals held 92.8–97.2 % at 1.7–2.8 points.
  - Write-up: `docs/phase1/models.md#by-group`.

## 2026-11-01

### Added (several models and the cheapest mix, #27)
- **`pgdocket estimate NAME --answer A --with V[,V]`**: one share from the active version's answers, the named versions' answers and the labels drawn at random.
  - **The estimator** is Multiple-PPI's: generalised least squares across the patterns of which sources a row has. With one model on every row, it is the regression estimator that PPI++ approximates.
  - **Its covariance.** The labels' relation to the models is a regression on the labelled rows, with half a disagreement added each way, and the models' covariances come from every row they judged.
  - **Which versions count.** A version is used only if the labelled rows it judged hold 10 with and 10 without the answer, and a version that repeats another adds nothing.
  - **The comparison.** It also reports each version with the labels alone, and the labels alone.
- **`--target SHARE --label-cost USD [--cost V=USD]`**: the cheapest mix for ± SHARE.
  - **What it gives.** How many labels, and how many rows each version should judge, from multifidelity Monte Carlo's nested sample sizes, capped at the table's rows. It is compared with judging every row and with labels alone.
  - **Costs per row** are each version's tokens at its backend's price. A free backend costs nothing unless `--cost` gives one, and work already done counts as paid for.
  - **`--draw CSV`** writes the next labels in the shared order, blind, for `label --random`.
- **`pgdocket backfill NAME --version V --sample N [--seed S]`**: the version judges only the first N rows of a shared random order, labelled rows first. Versions' samples and the labels then nest.
- **Validation** (`jevbench multivalidate`, no API calls): the 2,000 CFPB complaints with Jev (one row and 20 rows per request), Laya and the labels.
  - **Cost.** On 100 million rows, the cheapest mix for ± 2 points cost 68–93 % less than judging every row with Jev, and 32–63 % less than labels alone. On a million rows judging a sample saved 1–11 %: labels dominate.
  - **Coverage.** The combined estimate held 91–100 % on 200,000 resampled complaints, against 90–95 % for PPI++. Plans carried out held 93–98 % at the target width.
  - **A second model** pays only when far cheaper than the first. Batched Jev qualifies. Laya would need to cost under $18–71 per million rows, and takes 93 hours of this machine per million.
  - **Caught in testing:** covariances measured on different rows implied a correlation above 1, and intervals held 44 %. The regression-based covariance fixed it.
  - Write-up: `docs/phase1/models.md`.

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
