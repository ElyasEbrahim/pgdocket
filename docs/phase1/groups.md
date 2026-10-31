# Shares by group: where the labels go

*2026-10-31 · issue #25 · lineage: prediction-powered inference (Angelopoulos et al., 2023; PPI++), stratified PPI (Fisch et al., 2024), allocating human oversight across segments (Ye, Lyu & Tao, 2026), Multiple-PPI (Cowen-Breen et al., 2026), ABae (Kang et al., 2021), PPI as the difference estimator (Mozer, 2026) · evidence: [results/group-validation](../../results/group-validation/report.md)*

A question like "what share of complaints are about debt collection, by region?" doesn't need every row judged by the best model or checked by a person. It needs every *group's* figure to be right within a stated margin. How do mistakes on single rows reach that result, and which rows deserve the expensive work? Not the ones the model is least confident about. Three things decide it:

- **Systematic error.** A model that over-counts an answer gives a biased share however confident it is. Labels correct that. A model wrong more often in some groups needs each group corrected separately. One correction fitted on all the labels moves every group by the same amount, so it is wrong wherever the model's error differs, and in small groups errors don't average out (Mozer, 2026).
- **Where the labels fall.** A group's interval narrows with *its* labels. Labels drawn at random across the table arrive in proportion to group size, so the smallest group decides how many are needed. A database knows every group's size and every row's answer, so it can send each group the labels its own target needs: a Neyman allocation, as in stratified PPI (Fisch et al., 2024) and in allocating human oversight across segments (Ye, Lyu & Tao, 2026).
- **A floor for rare answers.** An interval holds once a group has at least 10 labelled rows with the answer and 10 without. For an answer 10 % of a group has, that is about 100 labels whatever the model.

## What pgdocket does

```bash
pgdocket estimate complaints --by region --answer "debt collection" --target 0.05 --draw next.csv
pgdocket label complaints --csv next.csv --stratified region     # after people fill in `label`
```

`--by` gives a share per value of a source column. Each group is corrected with its own labels: prediction-powered inference (PPI++) within the group, unbiased whatever the model gets wrong there. A group with every row labelled is exact. An all-groups line combines the groups, weighted by size. A group is flagged when the model's error there differs from its error in the other groups (a step-down z-test, Bonferroni across groups). Those are the groups one correction for all would get wrong.

`--target 0.05` says how many more labels each group needs for ± 5 points. A group with fewer than 30 labels gets a pilot first. After that, each group needs z² × (the per-label variance PPI++ leaves) / target² labels, at least 10 with and 10 without the answer, and never more than its rows. The plan also says how many labels drawn across the table would do the same, and which group sets that number. `--draw` writes the rows, drawn at random within each group and blind (no AI answer). `label --stratified` imports them as `stratified` labels (migration 010). With the group's random labels they form a random sample of the group, but not of the table. `estimate --by region` uses them; calibration, plain estimates, review plans and drift checks don't.

This is what it prints for the 2,000 CFPB complaints below, with Jev's answers and 400 labels drawn at random:

```
complaints: share of judged rows whose true answer is 'debt collection', by region (95 % intervals)
group                   rows  labels  model says  estimate      95 % interval     labels for ±5.0 pts
Midwest                  252      52       9.1 %     7.7 %         1.4 – 14.0                     +79  ⚠
Northeast                336      75       9.5 %    10.4 %         4.0 – 16.8                     +47  ⚠
South                    984     187      10.4 %    12.7 %         8.3 – 17.0                      +0
West                     420      84      13.3 %    12.8 %         7.3 – 18.2                      +8  ⚠
other or unknown           8       2      12.5 %         –                  –         +6 (all: exact)
⚠ fewer than 10 labelled rows with or without the answer: the interval may be too narrow
– : too few labels in that group to estimate it (fewer than 2, or all the same)
to reach ±5.0 points in every group: 140 more labels, drawn at random within each group (540 in all); drawn at random across all groups instead, about 2,000 would be needed (set by other or unknown, which random labels reach last)
```

## On real answers

The 2,000 CFPB complaints carry the product each consumer chose (the labels), their state (from the [CC0 source](https://huggingface.co/datasets/BEE-spoke-data/consumer-finance-complaints), mapped to US Census regions) and the year. Three stored sets of answers judge them: Jev 1.13 at 20 rows per request (Phase 0), Jev 1.13 one row per request, and [Laya](backends.md). Every label is known, so each method can be checked. `jevbench groupvalidate` runs it with no API calls ([report](../../results/group-validation/report.md)).

**Labels to bring every group to ± 5 points**, by `--target --draw` rounds (within groups) and by random labels added across the table until every group got there. Means over 100 runs; the groups' final intervals held the truth 98–100 % of the time either way.

| query | groups | across the table | within groups | fewer labels |
| --- | --- | ---: | ---: | ---: |
| debt collection, by region | the 4 regions | 1,068 | 626 | 41 % |
| debt collection, by region | and the 8 complaints from elsewhere | 1,786 | 651 | 64 % |
| credit card, by region | the 4 regions | 1,064 | 808 | 24 % |
| debt collection, by year | the 5 years with 100+ rows | 2,000 | 890 | 55 % |
| debt collection, by year | all 10 years (3 to 989 rows) | 2,000 | 1,005 | 50 % |

With Jev 1.13 one row per request; the other predictors are in the report.

- **Where the labels go matters more than which model judges.** Drawing within groups cut the labels needed by 24–64 %. The model's answers cut them by up to 22 % against labels alone (debt collection by region: 626 with Jev, 700 with Laya, 803 with labels alone). For the rarer credit-card answer, and by year, models barely helped (by year, 845–890 labels against 880–888 with labels alone): the floor of 10 labelled rows with and without the answer set the cost there.
- **Bias is not the problem; disagreement is.** Laya over-counts debt collection by 13–17 points in every region, and Jev under-counts it by 0–4. PPI++ removes both. What a model buys is agreement with people *within each group*, which shrinks the variance left after its answer.
- **One correction for all groups fails in small groups.** On these complaints the model's error was much the same across the four regions (no region was flagged with 400 labels), and one correction covered them. In the small groups it failed: with 800 labels drawn across the table, it held the truth 0–51 % of the time for 2015 (3 complaints) and 0–94 % for the 8 complaints from outside the regions, depending on the model and the answer. Each group's own labels cover them, at the price of labelling them, and a group of 3 or 8 is cheapest labelled whole.
- **Labels needed don't grow with the table.** Beyond the smallest groups, a group needs about z² × variance / target² labels, whatever its size. For 100 million complaints by region, that is still a few hundred labels per region; drawn across the table, the smallest region sets the total.

## How this relates to recent work

- **Multiple-PPI** (Cowen-Breen et al., ICLR 2026) splits a budget across several predictors of different cost and gold labels. As the budget grows, it moves from one cheap model to combinations. pgdocket judges every row with one model and spends the budget on labels. Choosing which model judges which rows is that paper's question, not built here.
- **Allocating human oversight** (Ye, Lyu & Tao, 2026) allocates labels across many AI-assisted segments with PPI. Its Neyman square-root rule uses each segment's rectification difficulty, with an upper-confidence-bound policy while the difficulties are unknown. The pilot-then-plan rounds here are the simple two-stage version, with the intervals checked by simulation.
- **ABae** (Kang et al., 2021) stratifies by a cheap proxy to answer aggregates with expensive predicates, including GROUP BY. It samples the expensive predicate; pgdocket's expensive step is the human label.
- **Semantic query engines** choose models per operator for cost and quality: Abacus in Palimpzest (Russo et al., 2026), Cortex AISQL's proxy-then-oracle cascades (Liskowski et al., 2026), and online aggregation with stratified sampling (OLLA, Hui et al., 2026). They report quality on benchmarks, not an interval for each group of a user's query from their own labels.

## Limits

- **Labels must be random within each group.** `--draw` makes them so. Rows labelled by hand or drawn by an audit are skipped by the draw only if labelled at random or for the same column. The groups must not change after drawing.
- **A group that only just reaches the floor by chance is less reliable than stated.** With 400 labels drawn across the table, 2020 (145 complaints) reached 10 debt-collection labels in only 31–35 of 500 draws. Its intervals in those draws held 86–90 % of the time: the draws that got there had more positives. Planning each group's labels up front avoids this.
- **Normal intervals**, as in `estimate`. Tiny groups are labelled whole and reported exactly.
- **Not built.**
  - Weighted queries, and rows counted several times by a join. Each row's influence on the result (its weight, or how often the join uses it) would multiply its share of the labels.
  - Choosing a cheaper model for some rows (Multiple-PPI).
  - Judging only a sample of rows when the statistic alone is wanted.

## Reproduce

```bash
docker compose run --rm --no-deps bench groupvalidate      # needs the source shard in data/raw/hf (jevbench fetch)
```
