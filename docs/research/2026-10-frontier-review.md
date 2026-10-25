# What recent research offers pgdocket

*2026-10-22 · issue #18 · experiments: [results/frontier-2026-10](../../results/frontier-2026-10/report.md) (`jevbench frontier`, no API calls) and [results/compare-k8s-jev-1.14](../../results/compare-k8s-jev-1.14/report.md) ($0.026)*

Before releasing 0.1.0a2, I surveyed recent work (mostly 2024–2026) on everything pgdocket does:
- guaranteed thresholds;
- counts from a few labels;
- comparing versions;
- calibration;
- semantic operators in databases;
- drift.

Each candidate had to fix a real gap and keep its guarantee. Where possible, I tested it on answers we already have: 500 labelled judgments each for Kubernetes routing and CFPB products, and three identical Phase 0 runs. One idea, a new model, needed a $0.026 run.

## Verdicts

| Idea | Source | Verdict | Evidence here |
| --- | --- | --- | --- |
| **Precision per answer**, not only overall | class-conditional conformal prediction (Ding et al., NeurIPS 2023) | **Adopted** (#19): reported and flagged everywhere; `--per-answer` guarantee | Under the overall 90 % guarantee, auto-accepted *testing* routes were right 54 % of the time; on CFPB, *credit card* 53 % |
| **Check a new model before switching** (jev-1.14.0, 14 Oct 2026) | TypeSafe docs | **Keep jev-1.13.0 for now**; switching is safe once there are enough labels to recalibrate | 1.14 changed 1.0 % of answers, the same as asking 1.13 again; accuracy 91.3 % → 91.7 % (all 300 labels) |
| **Review planning** with an error guarantee for the whole column | probably approximately correct labels (Candès, Ilyas & Zrnic, 2025) | **Adopted** (#20): `pgdocket plan` | ≤ 5 % errors on Kubernetes routing: review 36 % of issues with 200 labels (19 % if every label were known); the guarantee failed in 2.2 % of trials |
| Adaptive labelling for thresholds; **recall targets** | BARGAIN (Zeighami, Shankar & Parameswaran, SIGMOD 2026) | **Later**, with recall targets for yes/no decisions | not tested |
| **Drift alarms** from predictions plus a few labels | prediction-powered risk monitoring (Zhang et al., ICML 2026); Podkopaev & Ramdas | **Later** (Phase 3) | not tested |
| Stratified counts for rare answers | stratified PPI (Fisch et al., NeurIPS 2024) | **Later**: the next thing to try for counts | not tested |
| Re-running `calibrate` as labels arrive | anytime-valid risk control (Xu et al., NeurIPS 2024) | **No change needed** at these sizes | Stopping at the first threshold found: 0.6–4.2 % failures (≤ 5 % allowed) |
| Labelling where the model is unsure, for counts | active statistical inference (Zrnic & Candès 2024; Li et al., NeurIPS 2025; Gligorić et al., NAACL 2025) | **Not adopted** | 7–19 % narrower intervals for the two largest answers, but coverage fell to 45–63 % for *scheduling* |
| Asking twice and averaging | ensembling LLM labels | **Not adopted** | Accuracy 77.0 % → 76.9 %, calibration error 0.033 → 0.030, at twice the cost |
| Cheaper proxy models in a cascade | LOTUS, BARGAIN, streaming cascades | **Not now** | Jev already costs $24–43 per million rows |
| Calibration methods beyond temperature / Platt | 2025–2026 calibration papers | **No change** | Recent work still favours temperature scaling for accurate classifiers |

## 1. Precision per answer

pgdocket's guarantee is about *all* auto-accepted rows together: "precision ≥ 90 % with 95 % confidence". That holds. But a team acts on answers one at a time: it routes *testing* issues to SIG Testing. When most rows have a few large, easy answers, those carry the average and hide weak ones.

Kubernetes routing, overall target 90 % (threshold 0.769, 90 % of issues auto-accepted at 92.4 %):

| answer | auto-accepted | right | 95 % lower bound |
| --- | ---: | ---: | ---: |
| node | 147 | 95.9 % | 92.1 % |
| api-machinery | 113 | 91.2 % | 85.5 % |
| apps | 17 | 82.4 % | 60.4 % |
| instrumentation | 13 | 76.9 % | 50.5 % |
| **testing** | 13 | **53.8 %** | 28.7 % |

On CFPB at 80 %, *credit card* answers were right 53.3 % of the time (60 auto-accepted; lower bound 42 %), and *payday loan* 60 %.

**A guarantee per answer is honest but costly with few labels.** Certifying each answer on its own labels needs enough examples of it. With 500 labels, only the two largest Kubernetes answers and one CFPB answer qualify, and auto-acceptance falls from 90 % to 48 % (CFPB: 86 % → 51 %). Clustered conformal prediction (Ding et al.) pools similar answers to soften this.

**Proposal:**
1. Always report precision per answer for the auto-accepted rows, with its lower bound. Flag answers whose upper bound is below the target, which means they are demonstrably worse. Do this in `calibrate`, `check` and `compare`.
2. Offer `--per-answer` for a guarantee on each answer. Answers without enough labels then get no `auto`.

*Done 2026-10-22 (#19):*
- `calibrate`, `check`, `compare` and `promote` report precision per answer and flag weak answers; the re-analysed Kubernetes report flags *testing*.
- `calibrate --per-answer` stores a threshold per answer, the view applies it (migration 005), and `promote` keeps the mode. A CI simulation checks each answer's guarantee.

## 2. A new model: jev-1.14.0

TypeSafe released jev-1.14.0 on 14 October, at the same price, and plans to move `jev-latest` to it. Anyone calling `jev-latest` (pg-jev's default route) will change model without noticing. pgdocket pins the model id in each version's spec, and this is exactly the check `compare` exists for.

`pgdocket compare k8s_route 3` (v3 is jev-1.14.0, everything else identical): all 300 issues, $0.026 ([report](../../results/compare-k8s-jev-1.14/report.md), [validation](../../results/compare-k8s-jev-1.14/validation.md)).
- **Answers:** 1.0 % changed, against 1.0 % when 1.13 was asked again. That is no change beyond noise.
- **Accuracy:** the versions disagree on 3 issues, and 1.14 was right on 2 of them: +0.3 pp [−0.7, +1.7]. With all 300 labels, 91.3 % → 91.7 %.
- **Calibration:** raw error 0.060 → 0.045; with all labels, both auto-accept 95.0 % at ≥ 90 % precision.
- **A trap in the report.** On the 80 labels a user would have, 1.14 got *no* 90 % threshold at all, while 1.13 got 78 %. That's chance: 80 labels certify 90 % in only 54–66 % of random draws (see [versions.md](../phase1/versions.md)). The report now says so when fewer than 150 labels are used and only one version gets a threshold.
- **Decision:** keep 1.13 for now. Switching gains nothing measurable, and recalibrating 1.14 on 80 labels would leave `auto` off. v3 was retired.

Running this test also exposed a bug in `compare`, now fixed. Each row's noise baseline was the active version's *latest* re-ask. This run re-asked v1, so the earlier comparison's saved report would have changed when regenerated. Each row is now paired with the re-ask nearest in time to the new version's answer, and an integration test with a model that changes between two comparisons checks it.

## 3. Review planning with a guarantee

Probably approximately correct (PAC) labels guarantee the error of a *whole dataset*: AI answers where it's confident, people for the rest, with total error ≤ ε at confidence 1 − α. Our labels are a uniform random sample, so the bound reduces to a one-sided Clopper–Pearson on "kept by the AI and wrong". That rate falls monotonically as the cut rises, so the bounds need no correction across cuts.

| | AI-only error | target ε | review if every label were known | review, 100 labels | review, 200 labels | failures (≤ 5 %) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Kubernetes routing | 11.2 % | 5 % | 19 % | 45 % | 36 % | 2.9 % / 2.2 % |
| Kubernetes routing | 11.2 % | 3 % | 29 % | 67 % | 63 % | 3.1 % / 0.6 % |
| CFPB product | 20.2 % | 10 % | 24 % | 67 % | 46 % | 1.3 % / 1.9 % |
| CFPB product | 20.2 % | 5 % | 49 % | 96 % | 96 % | 0.4 % / 0.0 % |

This answers a question teams actually ask: *how much human review do I need for this column to be at most 5 % wrong?* The guarantee costs about twice the review an omniscient planner would need, and more labels narrow the gap. The paper's importance-weighted version lets people label where it matters most, if they record inclusion probabilities, as [ADR 0003](../adr/0003-human-labels-are-the-oracle.md) already plans. **Proposal:** `pgdocket plan NAME --max-error 0.05` gives the cut, how many rows to review and the review queue. *Done 2026-10-22 (#20): see [review-planning.md](../phase1/review-planning.md).*

## 4. Tested and not adopted

- **Re-running `calibrate` as labels arrive.** A user labels 10 more rows, re-runs, and deploys the first threshold found. Even then, the chance of deploying a threshold whose true precision is below target stayed within 5 %: 0.6–4.2 % across four settings, 1,000 trials each. Fixed-sequence testing with exact bounds, starting only where the target is provable, is conservative enough. Spending α across looks kept failures at 0 %, but found a threshold in only 0–54 % of trials, against 87–100 % without it. Anytime-valid betting methods (Xu et al.; Waudby-Smith & Ramdas) would cost less and are worth revisiting for continuous labelling. **Caveat:** 4.2 % at a 95 % target is close to the limit, and labelling row by row (rather than in batches of 10) wasn't tested.
- **Labelling where the model is unsure, for counts.** Rows were labelled with known probability, more often where the model's score was low, and a Horvitz–Thompson estimator corrected for it. For *node* the intervals were 7–11 % narrower, with coverage intact. For *api-machinery* they were 7–19 % narrower, but coverage slipped to 89–90 %, and for *scheduling* it fell to 45–63 %. Jev gives exactly 1.0 to most rows, so those rows get a tiny labelling probability, and the few that are wrong carry huge weights. Mixing half uniform sampling in (the robust version) helped but didn't fix it. Even with uniform labels this plain estimator under-covers small answers (*storage*: 41–85 %), which is why the product's estimator flags or refuses such cases. The current estimator, PPI++ on random labels with a reliability flag, stays. Stratified PPI is the next candidate for rare answers.
- **Asking twice.** Averaging two or three identical runs left accuracy unchanged (77.0 % → 76.9 % → 76.8 %), and lowered calibrated error only from 0.033 to 0.030 and 0.025. Those runs were batched, 20 rows per request. One row per request is already 2.4 points more accurate and has less run-to-run noise (about 1 %), so averaging would buy even less.
- **Cascades with cheap proxies** (LOTUS, BARGAIN, streaming cascades). These save money when the oracle is an expensive LLM. Jev is already the cheap model, at $0.042 per million tokens. One 2026 paper also reports that cascades can *raise* total cost when the proxy pass is itself LLM-heavy.

## 5. The landscape

- **Warehouse AI functions** (Snowflake `AI_CLASSIFY` / `AI_FILTER`, BigQuery `AI.CLASSIFY` / `AI.IF`, Databricks `ai_classify`) return a label or a boolean. Their documentation mentions no confidence score, no stored and versioned answers, and no guarantee. Without scores they can't be calibrated or thresholded; only label-based tools like `compare` and `estimate` would apply to them.
- **pgai** (Timescale / Tiger Data) calls LLMs from SQL and can classify rows from triggers. It stores what you write, with no calibration, versions or guarantees. Its vectorizer uses the same external-worker pattern pgdocket uses.
- **pg-jev** hasn't changed since v0.2.1 (3 Oct). It has about 1,200 stars, and its open issues ask for single-column use and a ClickHouse backend.
- **Semantic-operator systems** (LOTUS, BARGAIN, semantic SQL engines) give guarantees for *one query's* cost/accuracy trade-off. pgdocket's ground is different: answers that persist, stay current, are versioned and are calibrated on the user's own labels. None of the systems above does that.

## Update, 25 October 2026

A second sweep for work since this review, and what it led to:
- **Recall guarantees.** For LLM classifiers, the newest work controls false positives, not misses.
  - **ConformalGuard** (COLM 2026, [arXiv:2610.14365](https://arxiv.org/abs/2610.14365)) limits how often legitimate inputs trigger an action, and says explicitly that recall is not guaranteed.
  - It also observes that zero-shot LLM scores take only 10–11 distinct values, which fits our choice to set thresholds on raw scores.
  - The recall-target method comes from the selection-query line of work (SUPG; BARGAIN, SIGMOD 2026). **Adopted** as `calibrate --recall-target` (#21, [recall.md](../phase1/recall.md)): on three real yes/no tasks it never failed more than 3.6 % of the time (5 % allowed).
- **Silent model changes.** TypeSafe moved `jev-latest` to jev-1.14.0, so pg-jev users on the default alias got a new model.
  - **"Who Drifted: the System or the Judge?"** (preprint, June 2026, [arXiv:2606.15474](https://arxiv.org/abs/2606.15474)) watches for this kind of change with anytime-valid tests. It re-asks a fixed set of human-labelled rows and attributes a drop either to the product or to the model.
  - pgdocket pins model versions, and `compare` measured 1.14 against 1.13 (see §2). A continuous drift check on labelled rows is the natural next step. **Candidate.**

## What this means for the release

The per-answer report is a small change, and it fixes the most important gap this review found. Without it, `auto` can mislead a team about specific answers. I recommend adding it before 0.1.0a2. Review planning, recall targets, drift alarms and stratified counts are new features for after the release.

## Sources

- Ding, Angelopoulos, Bates, Jordan & Tibshirani. *Class-Conditional Conformal Prediction with Many Classes.* NeurIPS 2023. [arXiv:2306.09335](https://arxiv.org/abs/2306.09335)
- Candès, Ilyas & Zrnic. *Probably Approximately Correct Labels.* 2025. [arXiv:2506.10908](https://arxiv.org/abs/2506.10908)
- Zeighami, Shankar & Parameswaran. *Cut Costs, Not Accuracy: LLM-Powered Data Processing with Guarantees* (BARGAIN). SIGMOD 2026. [arXiv:2509.02896](https://arxiv.org/abs/2509.02896) · [code](https://github.com/ucbepic/BARGAIN)
- Xu, Karampatziakis & Mineiro. *Active, Anytime-Valid Risk Controlling Prediction Sets.* NeurIPS 2024. [arXiv:2406.10490](https://arxiv.org/abs/2406.10490)
- Waudby-Smith & Ramdas. *Estimating Means of Bounded Random Variables by Betting.* JRSS-B, 2024.
- Zrnic & Candès. *Active Statistical Inference.* ICML 2024.
- Li, Zrnic & Candès. *Robust Sampling for Active Statistical Inference.* NeurIPS 2025. [arXiv:2511.08991](https://arxiv.org/abs/2511.08991)
- Gligorić, Zrnic, Lee, Candès & Jurafsky. *Can Unconfident LLM Annotations Be Used for Confident Conclusions?* NAACL 2025. [ACL Anthology](https://aclanthology.org/2025.naacl-long.179)
- Fisch et al. *Stratified Prediction-Powered Inference for Hybrid Language Model Evaluation.* NeurIPS 2024. [paper](https://proceedings.neurips.cc/paper_files/paper/2024/file/c9fcd02e6445c7dfbad6986abee53d0d-Paper-Conference.pdf)
- Mozer. *PPI is the Difference Estimator.* 2026. [arXiv:2603.19160](https://arxiv.org/abs/2603.19160)
- Zhang, Cai, Yu & Simeone. *Prediction-Powered Risk Monitoring of Deployed Models for Detecting Harmful Distribution Shifts.* ICML 2026. [arXiv:2602.02229](https://arxiv.org/abs/2602.02229)
- Podkopaev & Ramdas. *Tracking the Risk of a Deployed Model and Detecting Harmful Distribution Shifts.* [arXiv:2110.06177](https://arxiv.org/abs/2110.06177)
- Patel et al. *Semantic Operators and Their Optimization* (LOTUS). VLDB 2025. [paper](https://www.vldb.org/pvldb/vol18/p4171-patel.pdf)
- Liskowski & Schmaus. *Streaming Model Cascades for Semantic SQL.* 2026. [arXiv:2604.00660](https://arxiv.org/abs/2604.00660)
- *Beyond Linear LLM Invocation: An Efficient and Effective Semantic Filter Paradigm.* 2026. [arXiv:2603.04799](https://arxiv.org/abs/2603.04799)
- *The Well-Tempered Classifier: Some Elementary Properties of Temperature Scaling.* 2026. [arXiv:2602.14862](https://arxiv.org/abs/2602.14862)
- TypeSafe. [Models](https://docs.typesafe.ai/models) (jev-1.14.0, accessed 2026-10-22).
- Snowflake [`AI_CLASSIFY`](https://docs.snowflake.com/en/sql-reference/functions/ai_classify) · Databricks [`ai_classify`](https://docs.databricks.com/gcp/sql/language-manual/functions/ai_classify) · Google Cloud, [BigQuery AI functions](https://cloud.google.com/blog/products/data-analytics/sql-reimagined-for-the-ai-era-with-bigquery-ai-functions) · [pgai](https://github.com/timescale/pgai)
