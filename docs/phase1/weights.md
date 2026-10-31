# Weighted shares: when a query counts a row many times

*2026-10-31 · issue #26 · lineage: sampling in proportion to size (Hansen & Hurwitz, 1943), sequential Poisson sampling (Ohlsson, 1998), sampling over joins (Chaudhuri, Motwani & Narasayya, 1999), prediction-powered inference (PPI++) · evidence: [results/weight-validation](../../results/weight-validation/report.md)*

Some queries count a judged row more than once. "Share of complaints about debt collection" may be run over judged texts, each standing for every complaint that carries it. "Share of revenue from tickets marked billing" counts each ticket once per dollar. Either way each row gets a weight: how often a join uses it, or what it sums. The share is Σ w·y / Σ w, and three things change:

- **One judgment, many rows.** A join copies a row's answer, and its mistake, to every row it pairs it with. The errors on those rows are one error, not many. Counting the copies as labelled rows treats one label as thousands. On the data below those intervals were 2–9 times too narrow (4.5 at the median) and held the truth 46 % of the time, down to 10 %.
- **The table's share is not the query's.** Credit reporting is 62 % of the judged texts and 94 % of the complaints they stand for. The texts' own share, weights ignored, held the weighted truth in 4 % of draws.
- **Labels drawn at random can't vouch for heavy rows.** The heaviest of the 2,000 texts stands for 13 % of the 21,980 complaints, and the heaviest 10 for 49 %. A random sample rarely includes them, and when it doesn't, neither the estimate nor its variance sees them. With labels drawn at random, the weighted estimate held 92 % pooled, down to 54 %.

So the rows that need a person are the ones the query counts most, not the ones the model is least sure of.

## What pgdocket does

```bash
pgdocket estimate texts --weight "(SELECT count(*) FROM complaints c WHERE c.text_hash = s.text_hash)" \
    --answer "debt collection" --target 0.02 --draw next.csv
pgdocket label texts --csv next.csv          # the file marks the rows drawn by weight
```

- **`--weight EXPR`** counts each row by an SQL expression on its source row `s`: a column to sum, or how often a join uses the row. Rows weighing 0 don't count, and negative weights are refused. With `--by COL`, each group gets its own weighted share.
- **From labels drawn at random**, rows heavier than every unlabelled row count exactly from their labels, since no sample stands in for them. The rest is PPI++ on weighted means. The estimate is trusted only while no unlabelled row holds more of the weight than the interval's half-width, since that row alone could move the share further. On the data below, this rule trusted 5 % of the draws, and those held the truth 99.4 % of the time.
- **`--target --draw` draws rows by weight.** This is sequential Poisson sampling. Each row has a permanent random number u from its key (`md5`, salted with `--seed`), rows are ranked by u / w, and the first ones are labelled. A row heavy enough is certain to be drawn and counts exactly; the others are drawn in proportion to weight. `label` imports them as `weighted` labels (migration 011). They are a sample of the query, not of the table, so calibration, plain estimates and drift ignore them.
- **The estimate reads the sample back from the ledger**: the run of labelled rows at the top of the ranking. Rows certain to be drawn count exactly, and the mean residual of the others estimates the rest (a Hájek estimator).
  - **The variance** adds half a disagreement each way, so a model that agreed with every row drawn doesn't claim zero error.
  - **The floor** is 5 rows drawn with the answer and 5 without.
  - **No record of the draw.** The ranking is recomputed from the data as it is now. More labels extend the same ranking, so rounds nest.
- **The plan** says how many rows drawn by weight the target needs. It works from the weights and from the disagreement rates measured on rows drawn by weight. A pilot of 30 comes first; after that each round asks for at least a quarter more rows than it has and at most four times as many.

Here it is on the data below, with Jev answering one row per request. First, after 400 labels drawn at random:

```
texts: share of judged rows whose true answer is 'debt collection', each counted by (SELECT count(*) FROM complaints c WHERE c.text_hash = s.text_hash) (95 % intervals)
group                   rows  labels     drawn  exact  model says  estimate      95 % interval     labels for ±2.0 pts
all rows               2,000     400 at random    0 %       4.9 %     2.8 %          1.3 – 4.2             +29 (pilot)  ⚠
drawn: how the labels the estimate uses were chosen; exact: the share of the weight in rows known exactly from their labels
⚠ an unlabelled row holds 13.0 % of the weight, more than the interval's half-width: labels drawn at random can't speak for it. Draw by weight (--target --draw).
to reach ±2.0 points: 29 more labels, drawn by weight (39 in all, 9 of them rows heavy enough to be certain); a pilot first, the rest planned from it
```

Then after three rounds drawn by weight, starting with no labels:

```
group                   rows  labels     drawn  exact  model says  estimate      95 % interval     labels for ±2.0 pts
all rows               2,000     152 by weight   81 %       4.9 %     1.0 %          0.2 – 1.7                      +0
```

The true share is 1.6 %. The model says 4.9 %, and the texts' own share is 12 %.

Some groupings live in the joined table: a text's copies come from many regions. For those, put the group in the weight and run one estimate per group, e.g. `--weight "(SELECT count(*) FROM complaints c WHERE c.text_hash = s.text_hash AND c.region = 'West')"`. The rankings share their random numbers, so rows drawn for one region tend to be drawn for the others too.

## On real answers

**The data.** The 2,000 CFPB narratives judged in Phase 0 are distinct texts. Of the source shard's 563,191 complaints, 21,980 carry one of them word for word: dispute letters sent to each credit bureau, templates, copies. 1,591 texts appear once, and one appears 2,866 times. Counted by weight, the 2,000 texts are worth 26 equal ones (Kish's effective sample size).

**The query.** Judging each text once and counting every complaint that carries it is a join with fan-out. The query is the share of those complaints about credit reporting, debt collection or credit card: over all of them (± 2 points) and over each region's (± 5 points). Each text's label and answer are taken for all its copies; the copies' own product agrees with the label on 99.5 % of them.

Three stored sets of answers judge the texts: Jev 1.13 at 20 rows per request, Jev 1.13 one row per request, and [Laya](backends.md). `jevbench weightvalidate` runs it with no API calls ([report](../../results/weight-validation/report.md)).

**Labels to reach the target.** Means over 100 runs; ranges over the 3 answers × 3 predictors, with how often the final intervals held the truth.

| query | texts | texts at random | drawn by weight (the plan) | complaints at random |
| --- | ---: | ---: | ---: | ---: |
| all complaints, ± 2 points | 2,000 | 1,882–1,956 (99–100 %) | **112–192** (96–100 %) | 111–160 (92–96 %) |
| Midwest, ± 5 | 336 | 290–334 (99–100 %) | **58–157** (97–100 %) | 54–132 (93–99 %) |
| Northeast, ± 5 | 432 | 385–423 (99–100 %) | **53–131** (97–100 %) | 54–118 (93–99 %) |
| South, ± 5 | 1,046 | 946–1,035 (100 %) | **81–195** (95–100 %) | 84–166 (93–100 %) |
| West, ± 5 | 501 | 412–478 (100 %) | **62–142** (92–100 %) | 58–145 (95–99 %) |

- **Drawing by weight needed 53–94 % fewer labels than texts drawn at random.** Texts at random only reach a trustworthy interval once nearly every heavy text happens to be labelled, which takes nearly all of them.
- **It came close to drawing the query's own rows.** Drawing complaints at random and labelling each one's text is the textbook design for a join's output, and held 92–100 %. The plan needed between 13 % fewer and 67 % more labels than it, 11 % more at the median. That design's sample is a set of draws counted with multiplicity, tied to one query's rows. The plan's is a run at the top of a ranking that the ledger can find again.
- **At fixed sizes**, 250 rows drawn by weight held the truth 94.6 % of the time or more in all 45 query, answer and predictor combinations; 150 rows held 93.8 % or more.
- **Which rows needed a person.** Mostly the heaviest texts: credit-reporting templates the models usually got right. Laya was wrong on 13 of the heaviest 40, and its mistakes covered 29 % of the complaints. Jev 1.13 was wrong on 2–4 of them. In the example above, rows certain to be drawn held 81 % of the weight.

## How this relates to other work

- **Sampling over joins.** Chaudhuri, Motwani & Narasayya (SIGMOD 1999) showed that a uniform sample of a join's input is not one of its output, and sampled the inputs in proportion to their join partners: the weights here. BaS (Zhu et al., 2026) gives approximate joins over unstructured data with guarantees, using blocking and importance sampling. There the model is the join predicate; here the join is on keys and the model labels one side.
- **Sampling in proportion to size** is Hansen & Hurwitz (1943); monetary-unit sampling in auditing applies it to amounts. Sequential Poisson sampling (Ohlsson, 1998), used for Sweden's consumer price index, fixes the sample size. Its permanent random numbers make rounds nest and let samples for different queries overlap.
- **PPI with nonuniform samples.** Kluger et al. (2025) give PPI intervals, by bootstrap, when the labelled sample is weighted, stratified or clustered. Design-based supervised learning (Egami et al., 2023) corrects LLM labels with expert labels drawn with known probabilities. Here the probabilities come from the query's weights.
- **Not adopted: the heaviest rows outright, the rest at random** (a take-all stratum, Hidiroglou, 1986). It was tested first. With the 100–200 heaviest texts labelled and 150–200 more drawn at random, intervals held 81–98 %: the texts just below the cut still spread too far in weight for a uniform sample.

## Limits

- **Weights must be known for every row and be non-negative.** When they change, the ranking changes and the labelled run at its top may shorten. The estimate stays valid for the data as it is now.
- **A row's label holds for all of its weight.** Exact copies share one truth (99.5 % here). A join whose rows differ in truth needs labels on those rows.
- **Normal intervals.** The floor of 5 rows drawn with and without the answer was chosen on this data: from 5 up, every combination held 93 % or more; below 5, 96 % pooled. Labels drawn at random keep the floor of 10, counted by weight.
- **Rounds stop at the first interval that meets the target.** The final intervals held 92–100 % (mean 98 %).
- **Not built.** Drawing the query's own rows, which was cheaper in some cells: its sample can't be read back from the labels alone. Groupings that live in the joined table run as one estimate per group.

## Reproduce

```bash
docker compose run --rm --no-deps bench weightvalidate      # needs the source shard in data/raw/hf (jevbench fetch)
```
