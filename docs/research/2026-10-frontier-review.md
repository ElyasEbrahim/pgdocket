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
| **Drift alarms** from predictions plus a few labels | prediction-powered risk monitoring (Zhang et al., ICML 2026); Podkopaev & Ramdas | **Adopted in a simpler form** (#22, 25 Oct): `pgdocket drift` | see the update below |
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
  - pgdocket pins model versions, and `compare` measured 1.14 against 1.13 (see §2). A continuous drift check on labelled rows was the natural next step.
  - **Adopted** as `pgdocket drift` (#22, [drift.md](../phase1/drift.md)).
    - **The model.** A fixed panel of labelled rows is asked again, and exact sign tests run on accuracy and on score direction. This is the canary approach of the "Who Drifted" preprint and of behavioural-shift audits ([arXiv:2410.19406](https://arxiv.org/abs/2410.19406)), with human labels to show *accuracy*, not only change.
    - **The rows.** The free check uses two-sample tests on the model's own outputs, which Rabanser et al. ("Failing Loudly", NeurIPS 2019, [arXiv:1810.11953](https://arxiv.org/abs/1810.11953)) found to be the most reliable simple shift detectors. A test per answer caught more than one chi-square over all answers (72 % against 49 % for a 20-point shift in one Kubernetes SIG, at the same false-alarm rate).
    - **Repeated checks** share one budget (α · 6 / (π² k²)) rather than an e-process. That is simpler, but power falls as checks repeat; the anytime-valid version is the next step.
    - **On stored answers:** false alarms were ≤ 2.6 %, CFPB's real rise in *credit reporting* complaints was caught, the 1.13 → 1.14 switch caused no alarm, and removing the option descriptions was caught from the scores.

## Update, 26 October 2026

A third sweep, for work on spending fewer human labels and on monitoring after deployment:
- **Labels stratified by the model's answer** ("Reducing Human Effort to Validate LLM Relevance Judgements via Stratified Sampling", up to 85 % fewer labels for an estimate; stratified PPI, Fisch et al.). No paper I found gives a finite-sample precision guarantee under such sampling.
  - **Why it looked promising.** Within one answer the labels stay a uniform sample, so per-answer guarantees stay exact, and rare answers could get enough labels.
  - **Result.** Tested (`jevbench stratvalidate`, [results/stratified-labels](../../results/stratified-labels/report.md)) and **not adopted**. It certified fewer answers than random labels: Kubernetes, 300 labels, 0.0 % of issues auto vs 30.1 %; CFPB, 2,000 complaints, 500 labels, 2.5 % vs 44.8 %.
  - **Why it failed.** Each answer needs ≥ 58 labelled rows above its threshold before it can be tested at 90 %. The small answers are mostly the inaccurate ones: even with every label known, per-answer mode could accept only 48–54 % of rows. Random labels also serve every other feature.
- **Monitoring the guarantee itself.** Harmful-shift tracking (Podkopaev & Ramdas, ICLR 2022) and prediction-powered risk monitoring (Zhang et al., ICML 2026) test whether a deployed model's error has risen, with labels that arrive over time. Recent anytime-valid detectors work the same way: predictive rank martingales (Kuang & Xia, [arXiv:2609.00536](https://arxiv.org/abs/2609.00536)) and an exponential martingale on non-conformity scores (Khan & Syed, [arXiv:2510.03839](https://arxiv.org/abs/2510.03839)).
  - **Adopted** as `pgdocket audit` (#23, [audit.md](../phase1/audit.md)). People label a few accepted rows per batch, blind, and an e-process tests "precision ≥ target". It is valid however often it is read.
  - **On real streams** it raised no false alarm. A fall to 70 % injected into the CFPB stream was caught in 93 % of audits.
  - **One lesson.** Bets sized from the all-time mean caught only 14 % of those falls, because months of good rows kept the mean high. Sizing them from the last 40 audited rows fixed it, and any bet chosen from past rows keeps the guarantee.
- **Blind labelling.** Pre-annotations anchor labellers (Berzak et al., EMNLP 2016). LLM suggestions shift the labels people give (Schroeder, Roy & Kabbara, 2025, [arXiv:2507.15821](https://arxiv.org/abs/2507.15821)). The audit file therefore leaves the AI's answer out.
- **Not yet.** Small-area smoothing for per-group accuracy (prediction-powered smoothing, Kawano et al., [arXiv:2609.20758](https://arxiv.org/abs/2609.20758)) could help when a team wants accuracy per segment. Audits that also use the model's scores on every accepted row (prediction-powered risk monitoring) could need fewer labels.

## Update, 27 October 2026

A fourth sweep, on the risk none of the earlier ones covered: text in a row aimed at the model.
- **Jev can be steered by what it judges.**
  - **Datadog Security Labs** ([2026-10-15](https://securitylabs.datadoghq.com/articles/jev-as-a-judge-how-counterfeit-policy-steers-jev/)): a counterfeit policy cut Jev's recall on prompt-injection benchmarks from 83 % to 3.9 %.
  - **Check Point** ([2026-09-24](https://blog.checkpoint.com/ai-security/jev-is-not-a-language-model-but-it-breaks-like-one-prompt-injection-against-a-typed-decision-model/)): adaptive attackers broke every configuration, at about $0.50 per break. Telling the model to distrust the document made no difference.
  - **HiddenLayer** (reported [2026-10-15](https://www.infoworld.com/article/4236103/typesafes-jev-even-more-susceptible-to-attack-than-llms-says-security-vendor.html)): adversarial text and injection disrupted Jev.
  - **Wu & Lim** ([arXiv:2609.28613](https://arxiv.org/abs/2609.28613)): on agent tasks, success was rare and tied to small decision margins.
  - **The gap.** None of them measured classification of table rows behind a calibrated threshold.
- **Measured** (`jevbench steer`, $0.74, [results/steer-k8s](../../results/steer-k8s/report.md)): on 300 labelled Kubernetes issues, a counterfeit routing policy appended by the issue's author steered 98.7 %, and `auto` accepted 93.3 %. A misleading claim with no instruction in it steered 72.3 %, "ignore previous instructions" 1.0 %. Steered answers were fully confident, so the threshold gave no protection.
- **Adopted** (#24, [steering.md](../phase1/steering.md), ADR 0005):
  - **`--untrusted`**, a distrust sentence: the policy fell to 1.3 %, and one aimed at the sentence to 2.0 %; claims still steered 33.7 %. That runs against Check Point's result, but our attacks were fixed templates and theirs adapted.
  - **`calibrate --harden`**, erase-and-check (Kumar et al., [arXiv:2309.02705](https://arxiv.org/abs/2309.02705)): it caught every steered row `auto` would have accepted, claims included, and sent 7.4 % of clean accepted rows to review. A payload written twice beats it, and the sentence stops that.
  - **Both together:** at most 0.3 % of issues got through `auto` for every attack except a misleading claim written twice (7.3 %).
  - **`check --steer`**, so teams can measure their own decision.
- **Not adopted.**
  - **Pattern filters:** circular evidence, and they miss claims.
  - **Keep-one voting:** it caught 85.7 % of the repeated payload but adds false flags. It is a candidate for later.

## Update, 30 October 2026

A fifth round, on a question every earlier one took for granted: do pgdocket's numbers mean the same with another model?
- **The gap.** Comparisons of Jev and Laya so far reviewed their published specifications (dos Santos Barbosa, [arXiv:2609.28940](https://arxiv.org/abs/2609.28940)) or came from vendors. None measured both on the same labelled rows. Laya's own README says its base checkpoints score near chance zero-shot on its typed-decisions benchmark.
- **Measured** (#4, [backends.md](../phase1/backends.md)): Laya 0.4.16, open weights, on this machine's CPU, against Jev on the same rows: three `check` tasks and the six Phase 0 experiments.
  - **Accuracy.** Zero-shot, Laya agreed with the labels 38.0 % of the time on Kubernetes routing (Jev 88.8 %) and 64.4–65.0 % on CFPB products (77–80 %).
  - **Calibration.** With 2,000 labels, isotonic calibration made Laya's probabilities as reliable as Jev's (error 0.018 against 0.027). It can't fix ranking: Laya's most confident routing answers were right 30.7 % of the time, so `calibrate` would certify nothing and send every issue to review.
  - **Per answer.** On CFPB, *credit reporting* passed 90 % on its own threshold: 36.4 % of complaints automatically, where no single threshold reached even 70 %.
  - **Order.** Laya is deterministic but twice as order-sensitive as Jev (14.2 % of answers change across three orders, against 7.4 % including Jev's noise). Averaging the orders gained it 1.6 points.
  - **Batches.** In a multi-row request Laya answers every question from the whole request, so pg-jev's 20-row batches give answers near chance.
  - **Steering** (31 October, [steering.md](../phase1/steering.md#another-backend-laya)). An issue's author steered Laya far less than Jev, mostly because Laya reads only the first ~300 tokens of a row: no appended attack steered a long issue. Written first, a counterfeit policy steered 21.3 %. `--untrusted` never reached the model, because Laya cut the instructions to make room for ten described options.
- **Adopted.**
  - `check` now says when more labels can't help, and points to per-answer guarantees when those work.
  - TypeSafe's key goes only to TypeSafe; pgdocket had been sending it to any backend.
  - Free backends show no API cost.
- **Not adopted, yet.**
  - **A Laya fine-tuned on the ledger's labels.** That is what Laya is built for.
  - **Laya's batch endpoint,** for speed.
  - **Order averaging as a per-backend option.**
  - **Cascades** (local model first, Jev for the rest) need a first stage that certifies something, and zero-shot Laya doesn't on these tasks.

## Update, 31 October 2026

A sixth sweep, on spending human labels where a query's result needs them: how mistakes on single rows reach a GROUP BY result.
- **The literature.**
  - **Across models.** Multiple-PPI (Cowen-Breen et al., ICLR 2026, [arXiv:2603.27414](https://arxiv.org/abs/2603.27414)) splits a budget across predictors of different cost and gold labels. Its active version routes each row to a predictor (Brawand et al., [arXiv:2605.08429](https://arxiv.org/abs/2605.08429)).
  - **Across segments.** Allocating human oversight in AI-enabled analytics (Ye, Lyu & Tao, [arXiv:2604.12497](https://arxiv.org/abs/2604.12497)) gives each segment labels by its rectification difficulty: a Neyman rule within tuned PPI.
  - **Subgroups.** Mozer ([arXiv:2603.19160](https://arxiv.org/abs/2603.19160)) shows PPI is the survey difference estimator and warns that prediction error differing by subgroup breaks a pooled correction.
  - **Systems.** ABae (Kang et al., PVLDB 2021) does proxy-stratified sampling for aggregates. Abacus (Palimpzest), Cortex AISQL and OLLA choose models or samples for cost and quality, without an interval per group from the user's labels.
- **Built** (#25, [groups.md](../phase1/groups.md)): `estimate --by COL` corrects each group with its own labels, flags groups whose model error differs, and `--target --draw` plans and draws each group's labels.
- **Measured** on 2,000 CFPB complaints by region and year, with the stored Jev and Laya answers:
  - **Allocation.** Labels drawn within groups brought every group to ± 5 points with 24–64 % fewer labels than random labels across the table; intervals held 98–100 %.
  - **The model.** It saved at most 22 %: what it buys is agreement with people within each group. For rare answers the floor of 10 labelled rows each way sets the cost.
  - **One correction for all groups** worked across the four regions, where errors were alike, and failed in small groups.
- **Then built** (#26, [weights.md](../phase1/weights.md)): weights and join fan-out as each row's influence on the result.
  - **The literature.** A join's output is not sampled by sampling its input (Chaudhuri, Motwani & Narasayya, SIGMOD 1999). Sampling in proportion to size (Hansen & Hurwitz, 1943) is the remedy, and sequential Poisson sampling (Ohlsson, 1998) makes it nest across rounds. PPI with nonuniform samples (Kluger et al., [arXiv:2501.18577](https://arxiv.org/abs/2501.18577)) and BaS for joins over unstructured data (Zhu et al., [arXiv:2603.16153](https://arxiv.org/abs/2603.16153)) are the nearest recent work.
  - **Measured** on the 2,000 CFPB texts and the 21,980 complaints in the source shard that carry them: drawing by weight took 53–94 % fewer labels than texts at random, and came within 13 % fewer to 67 % more of drawing the complaints themselves. The texts' own share held the weighted truth in 4 % of draws; copies counted as labelled rows, in 46 %.
- **Not built.** Judging a sample of rows instead of all; choosing a cheaper model per row (Multiple-PPI).

## What this means for the release

The per-answer report is a small change, and it fixes the most important gap this review found. Without it, `auto` can mislead a team about specific answers. I recommend adding it before 0.1.0a2. Review planning, recall targets, drift alarms and stratified counts are new features for after the release.

## Sources

The complete list, for every feature and with what each source was used for, is [REFERENCES.md](../../REFERENCES.md).


- Ding, Angelopoulos, Bates, Jordan & Tibshirani. *Class-Conditional Conformal Prediction with Many Classes.* NeurIPS 2023. [arXiv:2306.09335](https://arxiv.org/abs/2306.09335)
- Candès, Ilyas & Zrnic. *Probably Approximately Correct Labels.* 2025. [arXiv:2506.10908](https://arxiv.org/abs/2506.10908)
- Zeighami, Shankar & Parameswaran. *Cut Costs, Not Accuracy: LLM-Powered Data Processing with Guarantees* (BARGAIN). SIGMOD 2026. [arXiv:2509.02896](https://arxiv.org/abs/2509.02896) · [code](https://github.com/ucbepic/BARGAIN)
- Xu, Karampatziakis & Mineiro. *Active, Anytime-Valid Risk Controlling Prediction Sets.* NeurIPS 2024. [arXiv:2406.10490](https://arxiv.org/abs/2406.10490)
- Waudby-Smith & Ramdas. *Estimating Means of Bounded Random Variables by Betting.* JRSS-B, 2024. [arXiv:2010.09686](https://arxiv.org/abs/2010.09686)
- Zrnic & Candès. *Active Statistical Inference.* ICML 2024.
- Li, Zrnic & Candès. *Robust Sampling for Active Statistical Inference.* NeurIPS 2025. [arXiv:2511.08991](https://arxiv.org/abs/2511.08991)
- Gligorić, Zrnic, Lee, Candès & Jurafsky. *Can Unconfident LLM Annotations Be Used for Confident Conclusions?* NAACL 2025. [ACL Anthology](https://aclanthology.org/2025.naacl-long.179)
- Fisch et al. *Stratified Prediction-Powered Inference for Hybrid Language Model Evaluation.* NeurIPS 2024. [paper](https://proceedings.neurips.cc/paper_files/paper/2024/file/c9fcd02e6445c7dfbad6986abee53d0d-Paper-Conference.pdf)
- Mozer. *PPI is the Difference Estimator.* 2026. [arXiv:2603.19160](https://arxiv.org/abs/2603.19160)
- Zhang, Cai, Yu & Simeone. *Prediction-Powered Risk Monitoring of Deployed Models for Detecting Harmful Distribution Shifts.* ICML 2026. [arXiv:2602.02229](https://arxiv.org/abs/2602.02229)
- Podkopaev & Ramdas. *Tracking the Risk of a Deployed Model and Detecting Harmful Distribution Shifts.* ICLR 2022. [arXiv:2110.06177](https://arxiv.org/abs/2110.06177)
- Berzak, Huang, Barbu, Korhonen & Katz. *Anchoring and Agreement in Syntactic Annotations.* EMNLP 2016.
- Schroeder, Roy & Kabbara. *Just Put a Human in the Loop? Investigating LLM-Assisted Annotation for Subjective Tasks.* 2025. [arXiv:2507.15821](https://arxiv.org/abs/2507.15821)
- Kawano, Li & Parker. *Prediction-Powered Smoothing and Validation for Disaggregated AI Evaluation.* 2026. [arXiv:2609.20758](https://arxiv.org/abs/2609.20758)
- Kuang & Xia. *Anytime-Valid Distribution Shift Detection via Predictive Rank Martingales.* 2026. [arXiv:2609.00536](https://arxiv.org/abs/2609.00536)
- Khan & Syed. *Technical note on Sequential Test-Time Adaptation via Martingale-Driven Fisher Prompting.* 2025. [arXiv:2510.03839](https://arxiv.org/abs/2510.03839)
- Wu & Lim. *Decision Hijacking: Prompt Injection Attacks on Jev's Typed Probabilistic Decisions.* 2026. [arXiv:2609.28613](https://arxiv.org/abs/2609.28613)
- Kumar, Agarwal, Srinivas, Li, Feizi & Lakkaraju. *Certifying LLM Safety against Adversarial Prompting.* COLM 2024. [arXiv:2309.02705](https://arxiv.org/abs/2309.02705)
- Hines et al. *Defending Against Indirect Prompt Injection Attacks With Spotlighting.* 2024. [arXiv:2403.14720](https://arxiv.org/abs/2403.14720)
- Rabanser, Günnemann & Lipton. *Failing Loudly: An Empirical Study of Methods for Detecting Dataset Shift.* NeurIPS 2019. [arXiv:1810.11953](https://arxiv.org/abs/1810.11953)
- Li, Y. *Who Drifted: the System or the Judge? Anytime-Valid Attribution in LLM Evaluation Pipelines.* Preprint, 2026. [arXiv:2606.15474](https://arxiv.org/abs/2606.15474)
- Richter, He, Minervini & Kusner. *An Auditing Test To Detect Behavioral Shift in Language Models.* ICLR 2025. [arXiv:2410.19406](https://arxiv.org/abs/2410.19406)
- Patel et al. *Semantic Operators and Their Optimization* (LOTUS). VLDB 2025. [paper](https://www.vldb.org/pvldb/vol18/p4171-patel.pdf)
- Liskowski & Schmaus. *Streaming Model Cascades for Semantic SQL.* 2026. [arXiv:2604.00660](https://arxiv.org/abs/2604.00660)
- *Beyond Linear LLM Invocation: An Efficient and Effective Semantic Filter Paradigm.* 2026. [arXiv:2603.04799](https://arxiv.org/abs/2603.04799)
- *The Well-Tempered Classifier: Some Elementary Properties of Temperature Scaling.* 2026. [arXiv:2602.14862](https://arxiv.org/abs/2602.14862)
- TypeSafe. [Models](https://docs.typesafe.ai/models) (jev-1.14.0, accessed 2026-10-22).
- Snowflake [`AI_CLASSIFY`](https://docs.snowflake.com/en/sql-reference/functions/ai_classify) · Databricks [`ai_classify`](https://docs.databricks.com/gcp/sql/language-manual/functions/ai_classify) · Google Cloud, [BigQuery AI functions](https://cloud.google.com/blog/products/data-analytics/sql-reimagined-for-the-ai-era-with-bigquery-ai-functions) · [pgai](https://github.com/timescale/pgai)
