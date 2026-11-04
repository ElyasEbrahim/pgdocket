# Several models and labels: how many rows each should judge

*2026-11-01 · issue #27; by group 2026-11-03, #28; weighted 2026-11-04, #29 · lineage: double sampling (Neyman, 1938), multifidelity Monte Carlo (Peherstorfer, Willcox & Gunzburger, 2016), Multiple-PPI (Cowen-Breen et al., 2026), prediction-powered inference (PPI++) · evidence: [results/multi-validation](../../results/multi-validation/report.md), by region [results/multigroup-validation](../../results/multigroup-validation/report.md), weighted [results/multiweight-validation](../../results/multiweight-validation/report.md)*

A share's precision can be bought in three ways: human labels, which are dear and exact; a good model's answers, which are cheap per row; and a cheaper model's answers. Until now pgdocket judged every row with one model and spent the budget on labels. With several models of different cost, and very large tables, the question becomes how many rows each source should cover. Three things decide it:

- **Labels set most of the cost.** At $23.70 per million rows (Jev, one row per request), a label at $1 costs as much as 42,000 judgments. Up to about a million rows, judging every row adds a few percent to the bill.
- **A model needn't judge every row.** For a share, what a model's answers buy is their correlation with the labels. Once it has judged a random sample a few hundred times the size of the label sample, more rows buy almost nothing: this is double sampling (Neyman, 1938). On 100 million rows, judging every row would cost $2,400. On the data below, a sample 72–737 times the labels cost $0.40–11 and gave the same precision.
- **A second model must be cheaper, and add something.** An extra model pays only below a cost set by what it adds. That is (ρ_prev² − ρ²) / ρ² of the previous model's cost per row, ρ being each model's correlation with the labels (multifidelity Monte Carlo; Peherstorfer, Willcox & Gunzburger, 2016).
  - Jev at 20 rows per request ($13 per million rows) qualifies easily.
  - Laya, run on this machine's CPU, would have to cost under $18–71 per million rows, and it takes 93 hours here per million rows.

## What pgdocket does

```bash
pgdocket revise complaints --model jev-1.14.0                       # a second version: a better or a cheaper model
pgdocket backfill complaints --version 2 --sample 20000               # it judges the first 20,000 rows of a shared order
pgdocket estimate complaints --answer "debt collection" --with 2      # one estimate from the labels and both versions
pgdocket estimate complaints --answer "debt collection" --with 2 \
    --target 0.02 --label-cost 1 --draw next.csv                      # the cheapest mix, and the next labels
pgdocket label complaints --csv next.csv --random
```

- **`--with V`** estimates from the active version's answers, version V's answers and the labels drawn at random. This is Multiple-PPI's estimator for a given allocation: generalised least squares across the patterns of which sources a row has.
  - **Valid when** each source is a random sample of the scope: labels drawn at random, and versions judging every row or a `backfill --sample`.
  - **One model on every row** makes it the regression estimator that PPI++ approximates.
- **`backfill --sample N`** has a version judge the first N rows of one shared random order: labelled rows first, then the rest by md5 of the key and `--seed`. Samples of different versions and the labels are prefixes of that order, so they nest.
- **`--target --label-cost`** gives the cheapest mix for ± target: how many labels, and how many rows each version should judge.
  - **The method.** Multifidelity Monte Carlo, capped at the table's rows, compared with judging every row and with labels alone.
  - **Costs per row** come from the ledger: each version's tokens at TypeSafe's price. A free backend counts as free unless `--cost V=USD` sets a price, such as for the machine it runs on.
  - **Work already done** counts as paid for.
  - **A pilot first.** With fewer than 30 labels, the correlations aren't known yet: the plan is 30 labels, each version judging those rows (#28).
  - **Then a stop.** Once the interval is within ± target, with 10 labels each way, nothing more is needed.
- **`--draw`** writes the next labels in the shared order, blind, to import with `label --random`.
- **`--queue`** queues the rows the cheapest mix has each version judge, the first of the same order, for the worker (#28). Without it, the plan's `backfill --sample` commands are printed.
- **Safeguards.**
  - **Enough labels per version.** A version is used only if the labelled rows it judged include 10 with the answer and 10 without.
  - **No repeats.** A version that repeats another on every row it judged is left out: it adds nothing.
  - **A consistent covariance.** The labels' relation to the models is a regression on the labelled rows, with half a disagreement added each way. Covariances measured on different rows would otherwise imply a correlation above 1, and a model that agreed with every labelled row would claim no error. Both happened in testing: intervals held 44 % instead of 95 %.

Here it is on 2,000 CFPB complaints. Laya judges every row as the active version, Jev 1.13 judges 600 of them, and 200 labels lie inside those 600:

```
complaints: share of rows whose true answer is 'debt collection', from labels and 2 versions (95 % intervals); 2,000 rows
source                                      rows     says
v1 laya (active)                           2,000   26.0 %
v2 jev-1.13.0                                600    9.0 %
labels drawn at random                       200   11.5 %
estimate, every source together                    11.5 %  7.6 – 15.5
  v1 with the labels                               12.1 %  7.9 – 16.2
  v2 with the labels                               11.0 %  6.9 – 15.0
  the labels alone                                 11.5 %  7.0 – 16.0

the cheapest mix for ±3.0 points (a label $1.00; per row v1 free, v2 $0.000024):
  plan                                           labels         v1         v2  still costs
  labels, v2 on every row                           357          –      2,000      $157.03
  labels, v1 on every row                           385      2,000          –      $185.00
  labels alone                                      436          –          –      $236.00
cheapest: 157 more labels, 1,400 more rows judged by v2
```

The true share is 11.95 %. On a table this small, judging every row with Jev is cheapest. On a large one, the plan judges a sample.

## On real answers

The 2,000 CFPB complaints carry the product the consumer chose, which is the label. Three stored sets of answers judge them:
- Jev 1.13, one row per request: $23.70 per million rows;
- Jev 1.13 at 20 rows per request: $13.20 per million;
- Laya: no API cost, 0.34 s per row on this machine.

The shares estimated are credit reporting, debt collection, credit card and mortgage. Their correlations with the labels are 0.57–0.88 for Jev one row per request, 0.53–0.80 for Jev batched, and 0.40–0.76 for Laya. `jevbench multivalidate` runs everything below with no API calls ([report](../../results/multi-validation/report.md)).

**Coverage.** On 200,000 complaints resampled from the 2,000, with 300 draws per cell:
- **The combined estimate** held the truth 91–100 % of the time across the four answers and two sizes (150 labels in 3,000 judged rows; 400 in 20,000). That was with Jev on a sample, with Laya on every row as well, and with labels and Jev drawn independently of each other.
- **PPI++** with Jev on the same samples held 90–95 %. The lowest cells were credit reporting at 150 labels (91–92 % for both) and, for PPI++, mortgage at 400 (89.7 %).

**The cost of ± 2 points** (labels at $1):

| table | cheapest mix | Jev on every row | labels alone |
| --- | ---: | ---: | ---: |
| 10,000 rows | $182–1,267 (Jev on every row) | the same | $500–2,254 |
| 1 million rows | $183–1,136 | $206–1,149 | $500–2,254 |
| 100 million rows | **$183–1,136** | $2,553–3,495 | $500–2,254 |

Ranges over the four answers.
- **On 100 million rows, the cheapest mix cost 68–93 % less than judging every row**, and 32–63 % less than labels alone. Judging every row cost more than labels alone there. With labels at $0.25 the saving against judging every row was 89–98 %, and at $4, 34–76 %.
- **On a million rows, judging a sample saved 1–11 %** at $1 a label, because labels dominate.
- **The model saved at every size**: the cheapest mix cost 31–64 % less than labels alone, whatever the table.
- **A plan carried out delivered.** On the 200,000 resampled rows with labels at $1, each answer's cheapest plan was drawn and estimated 50–100 times. The intervals held the truth 93–98 % of the time, at an average half-width of 2.0 points (2.25 for mortgage, where the floor of 10 labels each way set the count).

**Which model goes where.**
- **Batched Jev, as a second model.** On cost alone it would pay for itself below $114–165 per million rows, and it costs $13. But the labels dominate, so it saved little. The plan chose it alone only for mortgage, where it is cheaper than Jev one row per request and nearly as good.
- **Laya** would need to cost under $18–71 per million rows: 93 hours of this machine's 12 cores per million rows would have to cost that little. Its errors also overlap Jev's: the two agree with a correlation of only 0.49–0.80, and its own correlation with the labels is lower.

## By group

*2026-11-03 · issue #28 · evidence: [results/multigroup-validation](../../results/multigroup-validation/report.md)*

A share by group must reach its precision in every group. Labels drawn within each group get there with far fewer labels than labels drawn across the table ([shares by group](groups.md), #25). `--with` now takes `--by`: each group is estimated from its own labels and from each version's answers on its rows, and planned on its own.

```bash
pgdocket estimate complaints --answer "debt collection" --with 2 --by region        # every region, from labels and both versions
pgdocket estimate complaints --answer "debt collection" --with 2 --by region \
    --target 0.03 --label-cost 1 --queue --draw next.csv                            # each region's cheapest mix, carried out
pgdocket worker                                                                    # v2 judges its sample in each region
pgdocket label complaints --csv next.csv --stratified region
```

- **Each group on its own.**
  - **Its labels** count when drawn at random from the table or within this column's groups (`label --stratified COL`, as `--draw` writes them).
  - **Its estimate** comes from those labels and each version's answers on the group's rows: Multiple-PPI's estimator, group by group.
  - **The whole scope** is the groups' estimates weighted by their rows.
  - **Groups where the active version's error differs** from the others' are named, as with `--by` alone.
- **Each group's cheapest mix** is planned from the group's own correlations; the total is their sum. Beside it are labels alone in every group and the active version on every row, which is what `estimate --by` plans. A group gets a pilot while it has fewer than 30 labels, is labelled whole when it has no more rows than that, and needs nothing more once its interval is within the target.
- **Nested within each group.**
  - **`--queue`** queues each version's sample in each group: the first rows of the group's shared order, labelled rows first, then by md5 of the key and `--seed`.
  - **`--draw`** writes the next labels in each group in the same order, for `label --stratified COL`.
  - So in every group, the labels lie inside each version's sample.

Here it is on the 1,992 complaints from the four US Census regions. Laya judges every row as the active version, Jev 1.13 judges 120–400 rows in each region, and 20–170 labels in each region lie inside those rows:

```
complaints: share of rows whose true answer is 'debt collection', by region, from labels and 2 versions (95 % intervals); 1,992 rows
group                     rows  labels  v1 says   v2 rows  v2 says  estimate    95 % interval  labels alone
Midwest                    252      20   27.8 %       120    9.2 %    10.0 %       0.0 – 24.5    0.0 – 24.5  ⚠
Northeast                  336     130   24.1 %       260   10.8 %    13.0 %       7.6 – 18.3    7.8 – 19.9
South                      984     170   25.5 %       400   10.8 %     9.4 %       5.5 – 13.4    4.0 – 12.4
West                       420     140   27.9 %       300   13.7 %    13.1 %       8.1 – 18.0    6.7 – 17.6
all groups               1,992     460                                10.9 %       7.8 – 13.9                ⚠
versions: v1 laya (active), v2 jev-1.13.0; labels: drawn at random, from the table or within each region
left out: v1 and v2 in Midwest (it says the same thing on every row there, or judged fewer than 10 labelled rows with and without the answer)
⚠ fewer than 10 labelled rows with or without the answer: the interval may be too narrow

the cheapest mix for ±4.0 points in every group (a label $1.00; per row v1 free, v2 $0.000024):
group                plan                                       labels        v1        v2  still costs
Midwest              pilot: 30 labels first                         30       252       120       $10.00
Northeast            labels, v2 on every row                       251         –       336      $121.00
South                nothing more: the target is met               170       984       400        $0.00
West                 labels, v2 on every row                       230         –       420       $90.00
in all: each group's cheapest mix                                  681                          $221.00
  labels alone in every group                                      744                          $284.00
  labels, v1 on every row (estimate --by)                          719                          $259.00
cheapest: 221 more labels, 196 more rows judged by v2; 1 group needs a pilot first, then plan again
```

The true shares are 11.1, 10.7, 11.9 and 13.6 %, and 11.95 % in all. The Midwest has only 20 labels, so its plan is a pilot. The South is already within ± 4 points. On a table this small, judging every row with Jev is cheapest; on a large one, the plan judges a sample.

### By region, on real answers

The same complaints and stored answers, by region: `jevbench multigroupvalidate` runs everything below with no API calls ([report](../../results/multigroup-validation/report.md)).
- **The goal:** the share of credit reporting, debt collection, credit card and mortgage complaints, in every region, to ± 3 points.
- **Two setups.** The active version answers every row, already paid for: Jev at 20 rows per request (setup A) or Laya (setup B). `--with` adds Jev one row per request, on a sample in each region, at $23.70 per million rows. Labels cost $1.

**Coverage.** On 200,000 complaints resampled region by region, with labels drawn within each region inside Jev's sample, 300 draws per cell:
- **300 labels per region,** inside 10,000 rows Jev judged: each region's interval held 91–100 % of the time, and the regions together 93–98 %.
- **100 labels per region,** inside 2,000 rows: 94–99 %, over the cells with enough draws holding 10 labels each way.
- **Narrower intervals.** Adding Jev's sample made them 3–39 % narrower than with the active version alone in setup A, and 6–49 % in setup B. Labels alone held 91–100 %.

**The cost of ± 3 points in every region** on 100 million rows (labels at $1; ranges over the four answers):

| setup | each region's cheapest mix | the active version alone (`--by`) | labels alone | one design across the table (`--with`) |
| --- | ---: | ---: | ---: | ---: |
| A: batched Jev on every row | $812–2,026 | $812–2,263 | $1,077–4,015 | $1,760–4,048 |
| B: Laya on every row | $812–2,033 | $812–2,688 | $1,077–4,015 | $1,682–4,051 |

- **Adding Jev on a sample saved 11–24 % where the active version is weak:** Laya, for credit reporting (24 %), debt collection (22 %) and credit card (11 %). Where the active version is nearly as good as Jev (batched Jev), it saved 3–10 %.
- **For mortgage it saved nothing.** At 3–8 % of complaints, the floor of 10 labels each way sets the count in every region: 315 labels in the Midwest. The interval ends at ± 1.7 points.
- **Drawing within regions cost 49–67 % less than one design drawn across the table.** Across the table, the smallest region sets how much is drawn everywhere, and that is all `--with` without `--by` can do.
- **Jev judged 29,000–239,000 rows** in all (none for mortgage), for $0.69–5.66; on every row, it would cost $2,371. A table of a million rows costs about the same, because labels set the cost.
- **Every region to ± 3 points costs 4.0–4.6 times** as much as the whole table to ± 3 points.

**In rounds.** The plans were carried out as `--target --queue --draw` would, on 100 million rows: a pilot, then each region's cheapest mix, planned again until nothing more was needed. Each answer ran 100 times, taking 3.1–3.7 rounds on average.
- **Cost:** $1,117–2,288 in setup A and $1,126–2,326 in setup B. That is 7–12 % and 4–21 % less than the same rounds with the active version alone.
- **The final intervals** held 92.8–97.2 % of the time, against 93.5–97.5 % with the active version alone. Their half-width was 1.7–2.8 points, and 99.2–100 % were within 5 % of the target.
- **Rounds cost more than a plan made from the true correlations** ($2,288 against $2,026 for credit reporting in setup A). A pilot's correlations are rough, and a plan only adds rows and labels.

## Weighted shares

*2026-11-04 · issue #29 · lineage: two-phase sampling (Särndal, Swensson & Wretman, 1992), sequential Poisson sampling (Ohlsson, 1998) · evidence: [results/multiweight-validation](../../results/multiweight-validation/report.md)*

Some queries count each row by a weight: a column they sum, or how often a join uses the row ([weighted shares](weights.md), #26). For those, labels are drawn in proportion to weight. Rows are ranked by a permanent random number over their weight, and the heaviest are certain to be drawn. `--with` now works there too, and the second version takes part in the same ranking.

```bash
pgdocket estimate complaints --answer "credit reporting" --weight copies --with 2 \
    --target 0.02 --label-cost 1 --queue --draw next.csv       # the labels' run and v2's run of one ranking
pgdocket worker                                                # v2 judges its run
pgdocket label complaints --csv next.csv                       # the file marks the rows drawn by weight
```

- **One ranking, three depths.**
  - The labels are its first rows.
  - The second version judges a longer run of it.
  - The active version judges every row.
  - Rows heavy enough to be certain to be labelled count exactly, as in #26. Because the runs nest, every labelled row also has the second version's answer.
- **The estimate** is the two-phase regression estimator for samples drawn in proportion to weight (Särndal, Swensson & Wretman, 1992). It is the labelled rows' share, corrected by how far both versions' answers on those rows stand from the versions' shares of the whole query.
  - **The active version's share** is known exactly from every row.
  - **The second version's share** comes from its run, regressed on the active version's answers.
  - **The variance** weighs each row by how likely it was to be drawn: nearly certain rows add little.
- **The second version counts once the labelled rows hold 10 with and 10 without the answer.** Until then the estimate is #26's, from the same labels.
- **The plan** finds the two cut-offs of the ranking that reach ± target most cheaply: how many labels, and how far down the second version judges.
  - **Certain rows.** Heavy rows become certain as the cut-offs move down, and the variance formula counts that. With no certain rows it is the same plan as without weights.
  - **A pilot first:** 30 rows drawn by weight, the second version judging them.
  - **Rounds:** at most four times as many labels drawn per round, as #26's plan takes them, and the next round plans again.
  - **A stop:** nothing more once the interval is within the target.
- **`--queue`** has the second version judge its run. **`--draw`** writes the labels' run, marked as drawn by weight for `label`.
- **One version.** With `--weight`, `--with` takes one version besides the active one.

Here it is on a million complaint texts with the CFPB join's fan-out (the 2,000 texts below, repeated 500 times), after a pilot and a first round. Laya judges every text as the active version. Jev 1.13 has judged the 180 texts at the top of the ranking, which also carry the labels:

```
complaints: share of the weight (copies) whose true answer is 'credit reporting', from labels drawn by weight, v1 on every row and v2 on a run of the ranking (95 % intervals)
group                   rows  labels  exact   v2 rows  v1 says  v2 says  estimate    95 % interval
all rows           1,000,000     180    0 %       180   69.1 %   88.0 %    92.9 %      89.4 – 96.4
labels: the run at the top of the ranking by u / w (seed 1); exact: the share of the weight in rows certain to be labelled; v2 rows: its run of the same ranking

the cheapest mix for ±2.0 points (a label $1.00; per row v2 $0.000024, v1 already on every row):
group              plan                                     labels    v2 rows  still costs
all rows           labels, v2 on a run                         397     29,558      $222.35
in all                                                         397     29,558      $222.35
  labels and v1 alone (estimate --weight)                      541        175      $365.58
  labels, v2 on every row                                      397  1,000,000      $245.07
cheapest: 222 more labels drawn by weight, 29,384 more rows judged by v2
```

The true share is 94.3 %. Labels with Laya alone would need 541. With Jev judging 29,600 texts of the ranking, 397 do, for $0.70 of Jev.

### On the CFPB join

The 2,000 CFPB texts judged in Phase 0 stand for the 21,980 complaints in the source shard that carry them word for word, one text 2,866 times ([weighted shares](weights.md)). `jevbench multiweightvalidate` runs everything below with no API calls ([report](../../results/multiweight-validation/report.md)).
- **The goal:** the share of these complaints about credit reporting (94.3 % of them), debt collection (1.6 %) or credit card (2.4 %), to ± 2 points.
- **Two setups.** The active version judges every text: batched Jev (setup A) or Laya (setup B). Jev one row per request judges a run of the ranking, at $23.70 per million texts. Labels cost $1.

**Coverage.** 100 or 200 labels at the top of a fresh ranking, inside Jev's run of 500 or 1,000 texts, 1,000 draws per cell:
- **The intervals held 93.8–99.4 % of the time,** and #26's from the same labels 95.6–99.4 %.
- **Where Jev counted, they were narrower** than #26's: 15–20 % for credit reporting, and 9–14 % for the rare answers at 200 labels. At 100 labels Jev seldom counted for the rare answers, and the intervals were the same.
- **How often Jev counted:** in 80–100 % of the draws for credit reporting, and in up to 67 % for the rare answers, whose labels seldom hold 10 complaints with the answer.
- **The estimate without the second version is #26's.** A two-phase form without it held only 81–94 % in the draws with 5–9 labelled rows on the rarer side.

**The cost of ± 2 points**, planned from the measured variances, on a million and on 100 million texts with this fan-out:
- **Credit reporting:** the cheapest mix had Jev judge 17,000–37,000 texts (under $1). It cost 12–13 % less than labels with batched Jev alone, and 19 % less than labels with Laya alone.
- **Credit card:** 5–6 % less with Laya, nothing with batched Jev.
- **Debt collection:** nothing. At 1.6 % of the complaints, the floor of 5 rows drawn with the answer sets the count, 319 labels.
- **Jev on every text** cost $233–386 on a million texts, and $2,580–2,743 on 100 million.

**In rounds** on a million texts: a pilot, then the cheapest cut-offs, planned again until met. Each answer and setup ran 100 times, taking 3.8–5.0 rounds on average.
- **Credit reporting cost 10 % less** with batched Jev and 17 % less with Laya. Credit card cost 2–4 % less. Debt collection cost 1 % less with batched Jev, and 7 % more with Laya.
- **The final intervals held 91–96 % of the time,** against 94–100 % with the active version alone, at half-widths of 1.2–1.9 points. Rounds stop at the first interval within the target, so a few come out a little narrow, as in #26.

**Why the gain is small here.**
- **Labels drawn by weight already make the heaviest texts exact:** 150 of them hold 83 % of the weight.
- **The weighted shares are extreme** (94 % and 2 %), so for the rare answers the floor of labels sets the count.
- **Jev one row per request is barely better than batched Jev here.** Their residual variances per labelled text drawn are 0.032 and 0.036.
- **Where it pays:** a weaker active version and a common answer, as with Laya on credit reporting. In the unit tests' heavy-tailed table, where the second model is much better, intervals were about 45 % narrower than #26's.

## How this relates to other work

- **Multiple-PPI** (Cowen-Breen et al., ICLR 2026) chooses which subsets of models to query on how many samples, and combines them with the minimum-variance linear estimator. pgdocket uses that estimator for whatever samples exist, and the nested special case for planning.
- **Active Multiple-PPI** (Brawand et al., 2026) routes each row to a subset of models by difficulty. Not built here: a row-by-row route makes each label's chance of being drawn depend on the row, which pgdocket's random labels avoid.
- **Multifidelity Monte Carlo** (Peherstorfer, Willcox & Gunzburger, 2016) gives the optimal nested sample sizes and the rule for which models to keep, in closed form. **Double sampling** (Neyman, 1938) is its one-model case: measure the cheap variable on a large sample, the dear one on a sub-sample.
- **Stratified multifidelity Monte Carlo** (Geraci, Schiavazzi & Zanoni, 2025) stratifies the inputs to reduce the variance of multifidelity estimates of one overall mean. With `--by`, each group has its own target instead. So each group's plan is its own multifidelity plan, and the cheapest total is their sum.
- **Stratified PPI** (Fisch et al., 2024) and **allocating human oversight across segments** (Ye, Lyu & Tao, 2026) allocate labels across strata for one model. `--with --by` does the same with several models of different cost.
- **OPAL** (Ma & Candès, 2026) learns which rows to label from a model's uncertainty, and gives intervals for groups such as demographic ones. pgdocket keeps each group's labels drawn at random, so its intervals need no per-row labelling probabilities.
- **Two-phase sampling** (Särndal, Swensson & Wretman, 1992) measures cheap variables on a first-phase sample and dear ones on a sub-sample of it, and corrects with a regression. With `--weight`, both phases are drawn in proportion to the query's weights. **Two-phase multiwave PPI** (Kluger & Bates, 2026) chooses each wave's probabilities from the data instead.
- **Cost-optimal active evaluation** (Angelopoulos et al., 2025) splits a budget between a weak and a strong rater for one mean. Semantic query engines choose models per operator for cost and quality (Abacus, Cortex AISQL), without an interval from the user's labels.

## Limits

- **Every source must be a random sample of the scope.** Use labels drawn at random, and versions judging every row or a `backfill --sample`. A version run with `compare` judges the labelled rows first and then a random sample, which also qualifies.
- **New rows are judged as they come.** The trigger queues every new row for every version, so `--sample` describes a table that isn't changing.
- **The plan assumes nested samples and costs that stay as measured.** Its intervals land near the target on average, and some come out a little wider.
- **By group, nothing is borrowed across groups.** Each group pays for its own precision in full, so small groups end up labelled whole and rare answers need the floor of 10 labels each way in every group. A model can't lower that floor.
- **With `--weight`, the second version counts only on its run at the top of the ranking.** Rows it judged elsewhere, such as a `backfill --sample`, don't count; `--queue` queues the run. It takes one second version.
- **Not built.** Routing rows to models one by one; a cost for a person's time other than a price per label.

## Reproduce

```bash
docker compose run --rm --no-deps bench multivalidate        # needs the stored Phase 0 and Laya runs (no API calls)
docker compose run --rm --no-deps bench multigroupvalidate   # by region: also needs the source shard in data/raw/hf (jevbench fetch)
docker compose run --rm --no-deps bench multiweightvalidate  # weighted: the same shard
```
