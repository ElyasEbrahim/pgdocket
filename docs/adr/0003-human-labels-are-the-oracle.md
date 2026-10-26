# ADR 0003: Human labels are the scarce oracle; every statistical claim derives from them

- Status: Proposed
- Date: 2026-10-17

## Context

Semantic query research (LOTUS, BARGAIN, SUPG-IT, Cortex AISQL cascades) treats the LLM as the expensive oracle. At Jev's price (~$0.042 per M input tokens, roughly $6–7 per million rows) model calls are close to free, so the scarce resource is now human verification. Studies applying Jev (e.g. police crash narratives, arXiv 2609.24052) found that calibration varies by model and task, and that recalibrating on task labels cut calibration error 3.3×.

## Decision

- Gold labels are drawn by stratified random sampling over predicted probability, and each label stores its **inclusion probability**. Pure uncertainty sampling is not used for gold, because it biases estimates. It may feed a separate "hard cases" queue that is never used for statistics.
- Calibration (Platt, isotonic, temperature) is fitted per (decision version × backend) on gold labels. Phase 0 showed that Jev reports probabilities on a 0.01 grid with exact zeros, which makes unfloored NLL-based fits (temperature) worse than no calibration. Floor at half the grid step before any NLL-based method. The Phase 0 learning curve showed that one-parameter calibrators (floored temperature for choice, Platt for yes/no) beat isotonic at every budget up to 500 labels, and about 100 labels already remove 45–85 % of the raw ECE. Default to parametric; offer isotonic once a decision has ≥ 1,000 labels.
- Thresholds with targets ("precision ≥ 0.95 with probability ≥ 1 − δ") use fixed-sequence testing with exact (Clopper–Pearson) or betting confidence bounds on gold labels.
- Aggregates (`estimate_count`, `estimate_share`) use prediction-powered inference, importance-weighted when sampling was stratified, and refuse to answer when gold coverage in the requested scope is too thin.
- Every guarantee is backed by a simulation coverage test in CI.
- *Implemented (2026-10-20):* `docket.estimate()` uses PPI++ with a normal-approximation interval. Simulations and 500 real Kubernetes judgments show it holds once the labels contain at least 10 rows with **and** 10 without the answer; below that, estimates are flagged `reliable = false`. With no labelled example on one side, no interval is given. Labels record their sampling (`random` / `all` / `manual`), and only `random` feeds estimates by default. Stratified sampling, to make rare answers estimable with fewer labels, is the next step.
- *Added (2026-10-26, #23):* **audits of accepted rows.**
  - **What.** `pgdocket audit` draws rows at random from those marked `auto` since the previous batch, and people label them blind.
  - **The test.** An e-process tests "precision ≥ target", anytime-valid by Ville's inequality, so the audit can be read after every batch.
  - **Labels.** Audit labels (`sampling = 'audit'`) come only from accepted rows, so no estimator other than the audit uses them.
  - **Not adopted.** Labels stratified by the model's answer would keep per-answer guarantees exact, but on real tasks they certified fewer answers than random labels at 150–500 labels.
- *Added (2026-10-25, #22):* **drift checks.** Two assumptions behind every guarantee are now checked: that new rows look like the labelled ones, and that the model answers as it did.
  - **The rows.** The check is free: recent rows against earlier ones, on the stored answers and scores, with a z-test per answer and a KS test on scores.
  - **The model.** Labels are the oracle here too. A fixed panel of rows with `random` or `all` labels is asked again, and exact sign tests on accuracy and on score direction compare each row's stored answer with its re-ask. `manual` labels are excluded, because hand-picked rows would break exchangeability.
  - **Repeated checks.** The k-th check since the last calibration spends α · 6 / (π² k²), so false alarms over the whole series stay ≤ α.
  - **Evidence.** On stored Jev answers, false alarms were ≤ 2.6 %, and real label shifts of 13–17 % were caught.
- *Added (2026-10-25, #21):* **recall targets for yes/no decisions.** The recall threshold is the highest score below which rows can be answered *no*, while at least the target share of real yeses stays above it. It uses fixed-sequence testing upward over the distinct scores, with a one-sided Clopper–Pearson bound on recall among the random-sample yeses, following the SUPG and BARGAIN line of work. Together with the precision threshold, the view gets three bands: auto-yes, review, and auto-no. On three real yes/no tasks, failures were at most 3.6 % (5 % allowed).
- *Added (2026-10-22, #20):* **review planning.** `pgdocket plan` keeps the model's most confident answers and sends the rest to people. It chooses the cut so that the whole column is at most ε wrong with 95 % confidence, using a one-sided Clopper–Pearson bound on the share kept and wrong (probably approximately correct labels, Candès et al. 2025). It uses random labels only. The importance-weighted version waits for labels that record inclusion probabilities.
- *Added (2026-10-22, #19):* **precision per answer.**
  - **Why.** A guarantee on all accepted rows together can hide an answer that is often wrong. On Kubernetes routing, *testing* was right 7 times in 13 under an overall 90 % guarantee; on CFPB at 80 %, *credit card* was right 32 times in 60.
  - **Reporting.** Every calibration and check now reports precision per answer among the accepted rows, with one-sided Clopper–Pearson bounds. It flags answers whose *upper* bound is below the target, which means they are demonstrably worse, not just thin on labels.
  - **Option.** `--per-answer` gives each answer its own threshold and guarantee (class-conditional, as in Mondrian / class-conditional conformal prediction). The view applies it (migration 005). Each answer's guarantee holds at 1 − α on its own, and a CI simulation checks it per answer.
  - **Cost.** Automation drops when labels per answer are few. Pooling similar answers (clustered conformal, Ding et al. 2023) is possible future work.
- *Changed (2026-10-22, #16):* the guaranteed threshold is chosen on the **model's own score** (top probability, or P(yes)), not on cross-fitted calibrated scores.
  - **Why the old rule was wrong.** The threshold was tested on one scale (each fold's temperature) and applied by the view on another (the final calibrator's). On real Kubernetes answers this left a threshold above every row's confidence (`auto` empty while `calibrate` reported 21.7 %). In principle it can also accept rows that were never tested. Whether a threshold existed also depended on the random fold assignment.
  - **Why the new rule holds.** The raw score doesn't depend on the labels, so fixed-sequence testing holds exactly. The view applies the very threshold that was tested, and a CI simulation checks the guarantee where the view applies it. Coverage was unchanged on real data (95.0 % at 300 labels).
  - **`check` too.** `pgdocket check` uses the same rule, so it reports the threshold `calibrate` would store. On the saved Kubernetes and CFPB reports, only the thresholds changed, and two stricter targets became reachable: tied top scores are no longer split at random by fold.
  - **Labels used.** `calibrate` uses `random` and `all` labels. Hand-picked ones (`manual`, such as the disagreements `compare` exports) need `--include-manual`. A label naming an option the version lacks counts as wrong for it.

## Consequences

- pgdocket's numbers are only as good as the sampling assumptions. The docs must state them: gold drawn from the same population, labels correct, distribution stable since labeling.
- Drift monitoring has to tell the user when gold labels are stale. `pgdocket drift` (#22) does this for the rows and for the model; its alarm says to label a fresh random sample.
- The review queue becomes a core feature rather than an add-on.
