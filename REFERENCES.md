# References

Every source pgdocket builds on, applies or measures itself against, grouped by the feature it shaped. Each entry says where it is used. Titles and authors of arXiv papers were checked against arXiv's metadata on 2026-10-27; venues are given where the paper or its arXiv record states one.

[Built on](#built-on) · [Data](#data) · [Thresholds and calibration](#thresholds-and-calibration) · [Counts](#counts-with-error-bars) · [Review planning](#review-planning) · [Recall targets](#recall-targets) · [Comparing versions](#comparing-versions) · [Drift checks](#drift-checks) · [Audits](#audits) · [Rows that steer the model](#rows-that-steer-the-model) · [Jev in applied studies](#jev-in-applied-studies) · [Landscape](#landscape)

## Built on

- **pg-jev.** [realZachi/pg-jev](https://github.com/realZachi/pg-jev), the Postgres extension pgdocket starts from (PostgreSQL License): its README, CHANGELOG, AGENTS.md, `sql/jev--0.2.1.sql`, `test/mock_api.py` and skill references. pgdocket speaks the same `/v1/systemone` API and borrows its request wording. Used throughout; teardown in [REPORT.md](REPORT.md).
- **pg-jev documentation.** [pgjev.com docs](https://pgjev.com/docs.md) · [Where it runs](https://pgjev.com/docs/getting-started/where-it-runs.md). Used in [REPORT.md](REPORT.md) and [ADR 0001](docs/adr/0001-external-worker-not-extension.md).
- **winkrun.** *不用向量索引，Postgres 也能做语义过滤：pg-jev 上手* (WeChat article, 4 October 2026; not redistributed here). Its list of pg-jev caveats shaped the design ([REPORT.md](REPORT.md)).
- **TypeSafe.** [Documentation](https://docs.typesafe.ai) · [Models](https://docs.typesafe.ai/models) (jev-1.13.0, jev-1.14.0) · [Jev 1.13 model jaggedness](https://docs.typesafe.ai/model-jaggedness/jev-1.13). The decision model every measurement here uses.

## Data

- **CFPB Consumer Complaint Database.** [consumerfinance.gov](https://www.consumerfinance.gov/data-research/consumer-complaints/). The bureau stopped publishing narratives in August 2026 ([American Banker](https://americanbanker.com/news/cfpb-will-no-longer-publish-consumer-complaint-narratives), [Sheppard Mullin](https://www.sheppard.com/insights/blogs/cfpb-stops-publishing-consumer-complaint-narratives-and-visualizations)). The 2,000 narratives used here come from a [CC0 mirror on Hugging Face](https://huggingface.co/datasets/BEE-spoke-data/consumer-finance-complaints). Used in Phase 0 ([findings](docs/phase0/FINDINGS-jev-1.13.md)), `check`, recall targets, drift and audits.
- **Kubernetes issues.** Public issues of [kubernetes/kubernetes](https://github.com/kubernetes/kubernetes), with the SIG and kind labels its maintainers applied, fetched with the GitHub CLI (`jevbench.ghissues`). Used in the [triage study](docs/phase1/kubernetes-triage.md) and every Phase 1 evaluation.

## Thresholds and calibration

*`check`, `calibrate`, per-answer guarantees ([ADR 0003](docs/adr/0003-human-labels-are-the-oracle.md), [versions](docs/phase1/versions.md)).*

- Clopper, C. J. & Pearson, E. S. (1934). *The use of confidence or fiducial limits illustrated in the case of the binomial.* Biometrika 26(4), 404–413. [doi:10.1093/biomet/26.4.404](https://doi.org/10.1093/biomet/26.4.404). One-sided exact bounds behind every precision and recall guarantee, and the review plan.
- Angelopoulos, A. N., Bates, S., Candès, E. J., Jordan, M. I. & Lei, L. (2021). *Learn then Test: Calibrating Predictive Algorithms to Achieve Risk Control.* [arXiv:2110.01052](https://arxiv.org/abs/2110.01052). Fixed-sequence testing of candidate thresholds without a multiple-testing correction.
- Guo, C., Pleiss, G., Sun, Y. & Weinberger, K. Q. (2017). *On Calibration of Modern Neural Networks.* ICML 2017. [arXiv:1706.04599](https://arxiv.org/abs/1706.04599). Temperature scaling, the calibrator for choice decisions.
- Platt, J. (1999). *Probabilistic Outputs for Support Vector Machines and Comparisons to Regularized Likelihood Methods.* In *Advances in Large Margin Classifiers*, MIT Press. The calibrator for yes/no decisions.
- Zadrozny, B. & Elkan, C. (2002). *Transforming classifier scores into accurate multiclass probability estimates.* KDD 2002, 694–699. Isotonic calibration, compared in Phase 0 and not the default.
- Naeini, M. P., Cooper, G. F. & Hauskrecht, M. (2015). *Obtaining Well Calibrated Probabilities Using Bayesian Binning.* AAAI 2015. [doi:10.1609/aaai.v29i1.9602](https://doi.org/10.1609/aaai.v29i1.9602). Expected calibration error, as reported by `check` and `calibrate`.
- Mattei, P.-A. & Loureiro, B. (2026). *The Well-Tempered Classifier: Some Elementary Properties of Temperature Scaling.* [arXiv:2602.14862](https://arxiv.org/abs/2602.14862). Why temperature scaling stays the default ([research review](docs/research/2026-10-frontier-review.md)).
- Ding, T., Angelopoulos, A. N., Bates, S., Jordan, M. I. & Tibshirani, R. J. (2023). *Class-Conditional Conformal Prediction with Many Classes.* NeurIPS 2023. [arXiv:2306.09335](https://arxiv.org/abs/2306.09335). The basis of per-answer guarantees (`calibrate --per-answer`).
- Xu, Z., Karampatziakis, N. & Mineiro, P. (2024). *Active, anytime-valid risk controlling prediction sets.* NeurIPS 2024. [arXiv:2406.10490](https://arxiv.org/abs/2406.10490). Why re-running `calibrate` as labels arrive needed no change.
- Bista, B., Jadhav, D. H., Choudhary, V., Saxena, G., Perumal, A. P., Subedi, A. & Ammineni, J. (2026). *ConformalGuard: False-Positive-Controlled Action Gating for LM Classifiers.* COLM 2026. [arXiv:2610.14365](https://arxiv.org/abs/2610.14365). Its observation that zero-shot LLM scores take few distinct values supports thresholds on raw scores.

## Counts with error bars

*`estimate` ([counts](docs/phase1/counts.md)).*

- Angelopoulos, A. N., Bates, S., Fannjiang, C., Jordan, M. I. & Zrnic, T. (2023). *Prediction-Powered Inference.* Science 382(6671), 669–674. [arXiv:2301.09633](https://arxiv.org/abs/2301.09633).
- Angelopoulos, A. N., Duchi, J. C. & Zrnic, T. (2023). *PPI++: Efficient Prediction-Powered Inference.* [arXiv:2311.01453](https://arxiv.org/abs/2311.01453). The estimator `docket.estimate()` implements.
- Fisch, A. et al. (2024). *Stratified Prediction-Powered Inference for Hybrid Language Model Evaluation.* NeurIPS 2024. [paper](https://proceedings.neurips.cc/paper_files/paper/2024/file/c9fcd02e6445c7dfbad6986abee53d0d-Paper-Conference.pdf). A candidate for rare answers.
- Zrnic, T. & Candès, E. J. (2024). *Active Statistical Inference.* ICML 2024. [arXiv:2403.03208](https://arxiv.org/abs/2403.03208). Tested; not adopted (coverage fell for rare answers).
- Li, P., Zrnic, T. & Candès, E. J. (2025). *Robust Sampling for Active Statistical Inference.* NeurIPS 2025. [arXiv:2511.08991](https://arxiv.org/abs/2511.08991). Tested with the above; not adopted.
- Gligorić, K., Zrnic, T., Lee, C., Candès, E. & Jurafsky, D. (2025). *Can Unconfident LLM Annotations Be Used for Confident Conclusions?* NAACL 2025. [ACL Anthology](https://aclanthology.org/2025.naacl-long.179). Tested with the above; not adopted.
- Mozer, R. (2026). *PPI is the Difference Estimator: Recognizing the Survey Sampling Roots of Prediction-Powered Inference.* [arXiv:2603.19160](https://arxiv.org/abs/2603.19160).
- Emmenegger, N., Stahler, E. & Podimata, C. (2026). *Prediction-Powered Inference Across Many Tasks for AI Evaluation & Social Science Research.* [arXiv:2605.29249](https://arxiv.org/abs/2605.29249).
- Kawano, S., Li, Z. R. & Parker, P. A. (2026). *Prediction-Powered Smoothing and Validation for Disaggregated AI Evaluation.* [arXiv:2609.20758](https://arxiv.org/abs/2609.20758). A candidate for accuracy per segment.
- *Reducing Human Effort to Validate LLM Relevance Judgements via Stratified Sampling* (book chapter). [Springer](https://www.springerprofessional.de/en/reducing-human-effort-to-validate-llm-relevance-judgements-via-s/52210330). Prompted the stratified-labels experiment, which was not adopted ([results](results/stratified-labels/report.md)).

## Review planning

*`plan` ([review planning](docs/phase1/review-planning.md)).*

- Candès, E. J., Ilyas, A. & Zrnic, T. (2025). *Probably Approximately Correct Labels.* [arXiv:2506.10908](https://arxiv.org/abs/2506.10908).

## Recall targets

*`calibrate --recall-target` ([recall](docs/phase1/recall.md)).*

- Kang, D., Gan, E., Bailis, P., Hashimoto, T. & Zaharia, M. (2020). *Approximate Selection with Guarantees using Proxies* (SUPG). PVLDB 13(11), 1990–2003. [arXiv:2004.00827](https://arxiv.org/abs/2004.00827).
- Zeighami, S., Shankar, S. & Parameswaran, A. (2026). *Cut Costs, Not Accuracy: LLM-Powered Data Processing with Guarantees* (BARGAIN). SIGMOD 2026. [arXiv:2509.02896](https://arxiv.org/abs/2509.02896) · [code](https://github.com/ucbepic/BARGAIN).

## Comparing versions

*`compare` ([versions](docs/phase1/versions.md)).*

- Dixon, W. J. & Mood, A. M. (1946). *The Statistical Sign Test.* Journal of the American Statistical Association 41, 557–566. [doi:10.1080/01621459.1946.10501898](https://doi.org/10.1080/01621459.1946.10501898). The exact test on disagreements; also the drift model check.
- Efron, B. (1979). *Bootstrap Methods: Another Look at the Jackknife.* The Annals of Statistics 7(1), 1–26. [doi:10.1214/aos/1176344552](https://doi.org/10.1214/aos/1176344552). Paired bootstrap intervals in `compare` and `check`.

## Drift checks

*`drift` ([drift](docs/phase1/drift.md)).*

- Rabanser, S., Günnemann, S. & Lipton, Z. C. (2019). *Failing Loudly: An Empirical Study of Methods for Detecting Dataset Shift.* NeurIPS 2019. [arXiv:1810.11953](https://arxiv.org/abs/1810.11953). Two-sample tests on a model's own outputs.
- Smirnov, N. (1948). *Table for Estimating the Goodness of Fit of Empirical Distributions.* The Annals of Mathematical Statistics 19(2), 279–281. [doi:10.1214/aoms/1177730256](https://doi.org/10.1214/aoms/1177730256). The two-sample Kolmogorov–Smirnov test on scores.
- Li, Y. (2026). *Who Drifted: the System or the Judge? Anytime-Valid Attribution in LLM Evaluation Pipelines.* [arXiv:2606.15474](https://arxiv.org/abs/2606.15474). Re-asking a fixed panel of human-labelled rows, the model check.
- Richter, L., He, X., Minervini, P. & Kusner, M. J. (2025). *An Auditing Test To Detect Behavioral Shift in Language Models.* ICLR 2025. [arXiv:2410.19406](https://arxiv.org/abs/2410.19406).
- Chauvin, T., Le Merrer, E., Taïani, F. & Tredan, G. (2026). *Log Probability Tracking of LLM APIs.* ICLR 2026. [arXiv:2512.03816](https://arxiv.org/abs/2512.03816).
- Dima, A., Foulds, J., Pan, S. & Feldman, P. (2025). *You've Changed: Detecting Modification of Black-Box Large Language Models.* [arXiv:2504.12335](https://arxiv.org/abs/2504.12335).
- Podkopaev, A. & Ramdas, A. (2022). *Tracking the risk of a deployed model and detecting harmful distribution shifts.* ICLR 2022. [arXiv:2110.06177](https://arxiv.org/abs/2110.06177). Also the basis of audits.
- Zhang, G., Cai, Y., Yu, G. & Simeone, O. (2026). *Prediction-Powered Risk Monitoring of Deployed Models for Detecting Harmful Distribution Shifts.* ICML 2026. [arXiv:2602.02229](https://arxiv.org/abs/2602.02229). A candidate for audits with fewer labels.
- Kuang, Q. & Xia, Y. (2026). *Anytime-Valid Distribution Shift Detection via Predictive Rank Martingales.* [arXiv:2609.00536](https://arxiv.org/abs/2609.00536).
- Khan, B. & Syed, T. Q. (2025). *Technical note on Sequential Test-Time Adaptation via Martingale-Driven Fisher Prompting.* [arXiv:2510.03839](https://arxiv.org/abs/2510.03839). Anytime-valid shift detection with an exponential martingale and Ville's inequality.

## Audits

*`audit` ([audit](docs/phase1/audit.md)).*

- Ville, J. (1939). *Étude critique de la notion de collectif.* Gauthier-Villars, Paris. Ville's inequality, which makes the audit valid however often it is read.
- Waudby-Smith, I. & Ramdas, A. (2024). *Estimating means of bounded random variables by betting.* Journal of the Royal Statistical Society Series B. [arXiv:2010.09686](https://arxiv.org/abs/2010.09686). The aGRAPA bets.
- Ramdas, A., Grünwald, P., Vovk, V. & Shafer, G. (2023). *Game-theoretic statistics and safe anytime-valid inference.* Statistical Science. [arXiv:2210.01948](https://arxiv.org/abs/2210.01948).
- Berzak, Y., Huang, Y., Barbu, A., Korhonen, A. & Katz, B. (2016). *Anchoring and Agreement in Syntactic Annotations.* EMNLP 2016. [paper](https://people.csail.mit.edu/berzak/papers/D16-1239.pdf). Why audit files leave the AI's answer out.
- Schroeder, H., Roy, D. & Kabbara, J. (2025). *Just Put a Human in the Loop? Investigating LLM-Assisted Annotation for Subjective Tasks.* [arXiv:2507.15821](https://arxiv.org/abs/2507.15821). The same, for LLM suggestions.

## Rows that steer the model

*`check --steer`, `--untrusted`, `calibrate --harden` ([steering](docs/phase1/steering.md), [ADR 0005](docs/adr/0005-row-text-is-untrusted.md)).*

- Frichette, N. & Al-Kassar, F. (2026-10-15). *Jev as a judge: how counterfeit policy steers Jev.* Datadog Security Labs. [article](https://securitylabs.datadoghq.com/articles/jev-as-a-judge-how-counterfeit-policy-steers-jev/). The counterfeit-policy attack.
- Check Point (2026-09-24). *A Decision Model Breaks Like Any Other Language Model: A First Look at Jev.* [blog](https://blog.checkpoint.com/ai-security/jev-is-not-a-language-model-but-it-breaks-like-one-prompt-injection-against-a-typed-decision-model/). Adaptive attackers; distrust instructions did not help.
- HiddenLayer research on Jev, as reported by InfoWorld (2026-10-15). [article](https://www.infoworld.com/article/4236103/typesafes-jev-even-more-susceptible-to-attack-than-llms-says-security-vendor.html).
- Wu, T. & Lim, W. Y. B. (2026). *Decision Hijacking: Prompt Injection Attacks on Jev's Typed Probabilistic Decisions.* [arXiv:2609.28613](https://arxiv.org/abs/2609.28613). Override markers; the decision-margin link.
- Kumar, A., Agarwal, C., Srinivas, S., Li, A. J., Feizi, S. & Lakkaraju, H. (2024). *Certifying LLM Safety against Adversarial Prompting.* COLM 2024. [arXiv:2309.02705](https://arxiv.org/abs/2309.02705). Erase-and-check, the basis of `--harden`.
- Hines, K., Lopez, G., Hall, M., Zarfati, F., Zunger, Y. & Kiciman, E. (2024). *Defending Against Indirect Prompt Injection Attacks With Spotlighting.* [arXiv:2403.14720](https://arxiv.org/abs/2403.14720). Marking text as data, the idea behind `--untrusted`.
- Baxi, P., Xu, J., Jiang, J. Y. & Jasin, S. (2026). *Prompt Injection in Automated Résumé Screening with Large Language Models: Single and Multi-Injection Settings.* Findings of ACL 2026. [arXiv:2606.27287](https://arxiv.org/abs/2606.27287). Injection against classification-style decisions.

## Jev in applied studies

*Phase 0 ([REPORT.md](REPORT.md), [ADR 0003](docs/adr/0003-human-labels-are-the-oracle.md)).*

- Rafe, A. & Das, S. (2026). *Calibrated Decisions at Scale: Converting Police Crash Narratives into Probabilistic Crash Variables with a System One Model (Jev).* [arXiv:2609.24052](https://arxiv.org/abs/2609.24052). Recalibrating on task labels cut calibration error 3.3×.
- Huang, J. et al. (2026). *Can Jev Judge Radiology Reports? Evaluating a System One Model for Clinical Factuality.* [arXiv:2609.27607](https://arxiv.org/abs/2609.27607).
- dos Santos Barbosa, J. A. (2026). *Calibrated Decision Models for Autonomous Penetration-Testing Harnesses: JEV and Laya as System One Decision Layers for LLM-Driven Pentest Agents.* [arXiv:2609.28940](https://arxiv.org/abs/2609.28940).

## Landscape

*Surveyed in Phase 0 ([REPORT.md](REPORT.md)) and in the [research review](docs/research/2026-10-frontier-review.md). Vendor and secondary sources are marked; Phase 0 exists to replace their claims with measurements.*

**Semantic query processing**
- Patel, L. et al. (2025). *Semantic Operators and Their Optimization: Enabling LLM-Based Data Processing with Accuracy Guarantees in LOTUS.* PVLDB 18. [paper](https://www.vldb.org/pvldb/vol18/p4171-patel.pdf) · [lotus-data/lotus](https://github.com/lotus-data/lotus).
- Liskowski, P. et al. (2026). *Cortex AISQL: A Production SQL Engine for Unstructured Data.* SIGMOD Companion '26 (Industry Track). [arXiv:2511.07663](https://arxiv.org/abs/2511.07663).
- Liskowski, P. & Schmaus, K. (2026). *Streaming Model Cascades for Semantic SQL.* [arXiv:2604.00660](https://arxiv.org/abs/2604.00660).
- Kim, K., Catheland, M. & Ailamaki, A. (2026). *Fast LLM-Based Semantic Filtering: From a Unified Framework to an Adaptive Two-Phase Method.* [arXiv:2606.08090](https://arxiv.org/abs/2606.08090).
- Hou, N., Zhao, K., Xie, J. & Yu, J. X. (2026). *Beyond Linear LLM Invocation: An Efficient and Effective Semantic Filter Paradigm.* [arXiv:2603.04799](https://arxiv.org/abs/2603.04799).
- Mang, Q. et al. (2026). *PLOP: Cost-Based Placement of Semantic Operators in Hybrid Query Plans.* [arXiv:2604.09944](https://arxiv.org/abs/2604.09944).
- Zimmerer, A. et al. (2026). *SemCEB: A Cardinality Estimation Benchmark for Semantic Operators.* [arXiv:2606.23081](https://arxiv.org/abs/2606.23081).
- Lin, Y. et al. (2026). *SEMA-SQL: Beyond Traditional Relational Querying with Large Language Models.* [arXiv:2604.23477](https://arxiv.org/abs/2604.23477).
- Ha, J., Park, Y. & Han, W.-S. (2026). *CADENZA: Compiling Natural-Language Intent into Task-Specific Operator DAGs for Semantic Query Processing.* SIGMOD 2027. [arXiv:2606.29151](https://arxiv.org/abs/2606.29151).
- Sanmartino, G., Urban, M., Papotti, P. & Binnig, C. (2026). *The Stretto Execution Engine for Semantic Data Systems.* [arXiv:2602.04430](https://arxiv.org/abs/2602.04430).

**Warehouse AI functions**
- Snowflake [Cortex AI functions](https://docs.snowflake.com/en/release-notes/2025/other/2025-06-02-cortex-aisql-public-preview), [`AI_CLASSIFY`](https://docs.snowflake.com/en/sql-reference/functions/ai_classify) · Databricks [`ai_classify`](https://docs.databricks.com/gcp/sql/language-manual/functions/ai_classify) · Google Cloud, [BigQuery AI functions](https://cloud.google.com/blog/products/data-analytics/sql-reimagined-for-the-ai-era-with-bigquery-ai-functions), [AlloyDB AI operators](https://docs.cloud.google.com/alloydb/docs/ai/evaluate-semantic-queries-ai-operators).

**Postgres ecosystem**
- [pgai](https://github.com/timescale/pgai), [pgai Vectorizer on any Postgres](https://www.tigerdata.com/blog/pgai-vectorizer-now-works-with-any-postgres-database) · [InterfazeAI/postgres-llm](https://github.com/JigsawStack/postgres-llm) · [pgEdge Vectorizer](https://github.com/pgEdge/pgedge-vectorizer) · Supabase [pg_net](https://supabase.com/docs/guides/database/extensions/pg_net) and [http](https://supabase.com/docs/guides/database/extensions/http) · [PostgreSQL 19 release notes (draft)](https://www.postgresql.org/docs/release/19/) · [Snowflake on PostgreSQL 19](https://www.snowflake.com/en/blog/engineering/postgresql-19-release-delay-feature-reverts/).

**Decision models and backends** (secondary and vendor sources)
- [DataCamp: Jev, TypeSafe's System One Model Explained](https://www.datacamp.com/blog/system-one-models-jev) · [TrueFoundry on Jev](https://www.truefoundry.com/blog/typesafe-ai-jev) · [Kuware: Jev, RLCD & Laya](https://kuware.com/blog/typesafe-jev-rlcd-laya-calibrated-decision-models/) · [Medium: Laya vs Jev](https://medium.com/data-science-in-your-pocket/laya-vs-typesafe-jev-ai-8b5dd9ce0176) · [Zeli: "Jev's calibrated probabilities are a myth"](https://zeli.app/story/49816899) (unverified).
- [Firecrawl: OpenAI Decisions API vs Jev](https://www.firecrawl.dev/blog/openai-decisions-api-vs-jev) · [AI Weekly: Decisions API public beta](https://aiweekly.co/alerts/openai-launches-decisions-api-public-beta-on-gpt-6-luna) · [eesel: OpenAI Decisions API](https://www.eesel.ai/blog/openai-decisions-api).
- [OrcaRouter: Laya explained](https://www.orcarouter.ai/blog/laya-decision-model-explained) · [OrcaRouter: Laya vs Decider](https://www.orcarouter.ai/blog/laya-vs-decider) · [ai-tldr: Laya](https://ai-tldr.dev/models/laya/) · [Laya model card mirror](https://huggingface.co/tt-hous/laya-p150) · [bladedevoff/stuntd](https://github.com/bladedevoff/stuntd), a `/v1/systemone`-compatible server.

**Regulation**
- [Gibson Dunn: EU AI Act omnibus agreement](https://www.gibsondunn.com/wp-content/uploads/2026/05/eu-ai-act-omnibus-agreement-postponed-high-risk-deadlines-and-other-key-changes.pdf) · [Sidley: provisional agreement to delay AI Act obligations](https://datamatters.sidley.com/?p=8999).
