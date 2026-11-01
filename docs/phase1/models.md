# Several models and labels: how many rows each should judge

*2026-11-01 · issue #27 · lineage: double sampling (Neyman, 1938), multifidelity Monte Carlo (Peherstorfer, Willcox & Gunzburger, 2016), Multiple-PPI (Cowen-Breen et al., 2026), prediction-powered inference (PPI++) · evidence: [results/multi-validation](../../results/multi-validation/report.md)*

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
- **`--draw`** writes the next labels in the shared order, blind, to import with `label --random`.
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

## How this relates to other work

- **Multiple-PPI** (Cowen-Breen et al., ICLR 2026) chooses which subsets of models to query on how many samples, and combines them with the minimum-variance linear estimator. pgdocket uses that estimator for whatever samples exist, and the nested special case for planning.
- **Active Multiple-PPI** (Brawand et al., 2026) routes each row to a subset of models by difficulty. Not built here: a row-by-row route makes each label's chance of being drawn depend on the row, which pgdocket's random labels avoid.
- **Multifidelity Monte Carlo** (Peherstorfer, Willcox & Gunzburger, 2016) gives the optimal nested sample sizes and the rule for which models to keep, in closed form. **Double sampling** (Neyman, 1938) is its one-model case: measure the cheap variable on a large sample, the dear one on a sub-sample.
- **Cost-optimal active evaluation** (Angelopoulos et al., 2025) splits a budget between a weak and a strong rater for one mean. Semantic query engines choose models per operator for cost and quality (Abacus, Cortex AISQL), without an interval from the user's labels.

## Limits

- **Every source must be a random sample of the scope.** Use labels drawn at random, and versions judging every row or a `backfill --sample`. A version run with `compare` judges the labelled rows first and then a random sample, which also qualifies.
- **New rows are judged as they come.** The trigger queues every new row for every version, so `--sample` describes a table that isn't changing.
- **The plan assumes nested samples and costs that stay as measured.** Its intervals land near the target on average, and some come out a little wider.
- **Not built.** `--with` together with `--by` or `--weight`; routing rows to models one by one; a cost for a person's time other than a price per label.

## Reproduce

```bash
docker compose run --rm --no-deps bench multivalidate        # needs the stored Phase 0 and Laya runs (no API calls)
```
