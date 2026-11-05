# Rare answers: intervals that need no floor

*2026-11-05 · issues #30, #31, #32 · lineage: estimating means by betting (Waudby-Smith & Ramdas, 2024), prediction-powered inference's nonasymptotic variant (Angelopoulos et al., 2023), betting for weighted sampling without replacement (Shekhar et al., 2023) · evidence: [results/betting-validation](../../results/betting-validation/report.md), [results/rare-validation](../../results/rare-validation/report.md), [results/sql-betting](../../results/sql-betting/report.md)*

PPI++'s interval, estimate ± z·se, rests on the central limit theorem. It holds once the labels contain enough rows with and without the answer, so pgdocket waits for 10 of each before trusting one. For a rare answer that floor, not the target, sets how many labels are needed:
- an answer on 1.8 % of rows needs about 550 labels before the interval can be trusted, whatever precision was asked for;
- below the floor the normal interval is often wrong. On real answers at 50–200 labels it held the truth in 28–98 % of draws (median 87 %), not 95 %.

## What pgdocket does

Below the floor, `estimate` and `estimate --by` give a **betting interval** instead, marked `(betting)`. So does `docket.estimate()` in SQL, with `method = 'betting'` (#32). Here it is on the 300 Kubernetes issues of the live replay, judged by Jev, with 80 labels drawn at random:

```
k8s_route: how many judged rows truly have each answer (95 % intervals)
answer                 model says   estimate         95 % interval           labels only     labels
node                          102        114              92 – 136              70 – 133   27/80
api-machinery                  74         74               57 – 91              53 – 112   22/80
scheduling                     58         57               43 – 70               24 – 73   13/80
network                        14         14                3 – 37                0 – 24    3/80     (betting)
storage                        11          7                7 – 42                8 – 45    7/80     (betting)
apps                           11          5                1 – 30                0 – 11    1/80     (betting)
auth                           10         10                4 – 30                0 – 24    3/80     (betting)
cli                             7          3                3 – 33                0 – 24    3/80     (betting)
testing                        10          3                1 – 22                0 – 11    1/80     (betting)
instrumentation                 3          0                0 – 15                     –    0/80     (betting)
judged rows in scope: 300 of 300
(betting): fewer than 10 labelled rows with or without that answer, so the interval is by betting: valid at any number of labels, and wider than the usual one would be once there are enough
```

- **Every interval holds the true count:** network 14, storage 11, apps 10, auth 9, testing 7, cli 6, instrumentation 2.
- **What it replaced.** Before, the seven rare SIGs got a normal interval marked ⚠. Instrumentation, with no labelled issue, got no interval at all.
- **The estimate stays PPI++'s, moved inside the interval where it falls outside.** Storage's 7 sits at the interval's lower end.

- **How it works.** For each candidate share, a gambler bets on the labelled rows, one at a time, that the share is wrong. If it were the true share, no strategy would ever multiply its capital by 20 except with probability 5 % (Ville's inequality). So the shares at which the capital never reaches 20 form a 95 % confidence set.
- **Valid at any number of labels, and wherever the labelling stops.** The bets don't depend on how many labels there will be, so rounds that stop once the target is met keep the guarantee.
- **It uses the model.** As in prediction-powered inference's nonasymptotic variant (Angelopoulos et al., 2023), the bets are on the rectifier y − λ·f: the label minus λ times the model's answer.
  - **λ** is fitted to the labels before each one, which keeps the guarantee.
  - **The model's share of the scope** is known from every row, so the whole error budget goes to the labels.
- **Without replacement.** Labels are drawn from a finite scope, so each bet is against the share left among the rows not yet labelled (Waudby-Smith & Ramdas, 2024, §5). Once every row is labelled the interval closes on the true share.
- **In the order drawn.** The labels are taken in the order they were drawn: when they were imported, then by a hash of the key.
- **Only below the floor.** Once the normal interval holds it is narrower: a betting interval for an answer on 30 % of rows is about 1.7 times as wide at 400 labels. So the normal one stays where it is trusted.
- **The plan takes the cheaper way.** `estimate --by --target` asks, for each group below the floor, whether betting would reach ± target with fewer labels than the floor needs, and plans for that. It predicts the width from a typical draw at the rates the labels show.

## On real answers

`jevbench bettingvalidate` runs everything below with no API calls ([report](../../results/betting-validation/report.md)). It uses stored answers whose true labels are known: 500 Kubernetes issues (which SIG) and 2,000 CFPB complaints (which product), each answered by Jev 1.13 and by Laya.

**Does it hold?** For every answer, with 50, 100, 200 and 300 labels drawn at random, 500 times each:
- **The betting interval held 95.4–100 %** in every one of the 152 cells.
- **The normal interval below the floor did not.** For answers on under 8 % of rows at up to 200 labels, it held 28–98 % of the draws below the floor (median 87 %). For payday loans (1.2 % of complaints) it held 28 % at 50 labels and 64 % at 100.
- **What pgdocket now reports held 92.4 % or more.** That is the normal interval above the floor, betting below it. The six cells under 95 % are above the floor in at least 98.6 % of their draws, at 50–300 labels for answers on 6–62 % of rows: the normal interval's own small-sample misses, as before.

**Does it save labels?** Shares by group, round after round until every group is within ± target, as #25 measured them. These are CFPB complaints by US region (4 regions and 8 complaints from elsewhere) and by year (10 years). Each answer ran 100 times per cell with each model:

| answer | share of complaints | ± 5 points | ± 3 points |
| --- | ---: | ---: | ---: |
| student loan | 1.8 % | 64–71 % fewer labels | 41–46 % fewer |
| mortgage | 5.5 % | 34–39 % fewer | 6–14 % fewer |
| credit card | 7.1 % | 21–27 % fewer | 0–3 % fewer |
| debt collection | 12 % | 4–9 % fewer | the same |

- **The rarer the answer and the looser the target, the more betting saves.** Betting pays where a ± target interval needs fewer labels than 10 rows with the answer do. Roughly, that means an answer on fewer rows than the target is wide, or a little more where the model helps.
- **Final intervals held 97.0 % or more** with betting, and 98.0 % with the floor alone.
- **Never worse.** The plan takes betting's route only where it is cheaper. Where it isn't (debt collection at ± 3), it takes the same labels as before.

**Found on the way: the plan stopped short of its target.** In the first runs, many groups ended at ± 5.0–5.5 points for a ± 5 target, with or without betting. In some cells only 8 % of runs reached every group.
- **The shortfall.** The plan's formula, z² × the variance per label the model leaves ÷ target², leaves out the model's own spread on the unlabelled rows. The interval counts that spread.
- **The floor.** It was judged from the estimated share, so a group with 8 of the 10 labelled rows it needed could be told it had enough.
- **The fix.** A group short of its target now asks for n × (half-width ÷ target)² labels, and at least a tenth more. Below the floor, it asks for the rows still missing on top of its labels. Runs now reach every target 99–100 % of the time.

## Weighted shares and several models (#31)

Three estimates kept floors of their own after #30:
- **`--weight` with labels drawn by weight** trusts its interval from 5 rows drawn with the answer and 5 without.
- **`--weight` with labels drawn at random** needs 10 each way counted by weight, and no unlabelled row heavier than the interval's half-width.
- **`--with`** can't weigh any version against the labels until they hold 10 rows each way. Until then the estimate was the labels' alone, flagged.

Below those floors they now give betting intervals too, marked `(betting)`, and their plans take whichever route reaches the target first.

**Labels drawn by weight.** #30's interval assumes each label is drawn at random from the rows not yet labelled. Rows drawn by weight are not: pgdocket ranks rows by u / w and labels the first ones (sequential Poisson sampling, Ohlsson 1998).
- **Scoring each draw by its probability.** Shekhar, Xu, Lipton, Liang & Ramdas (2023) build betting intervals for a weighted mean under any design that draws without replacement. They score each draw by its value over its probability of being drawn next, which keeps the gambler's capital a fair bet at the true share. pgdocket uses that, with the model's answers as the control variate y − λ·f.
- **What that probability is here.** A row not drawn by rank t has its rank uniform between t and 1 / w. So the row drawn at rank t is row i with probability in proportion to w / (1 − t·w): in proportion to weight while t·w is small, and likelier as a row nears certainty.
  - For the row drawn, t·w is its own u, so each label's score is scaled by (1 − u).
  - What the true share implies for that score needs one sum over the rows not yet drawn, Σ w / (1 − t·w). The heaviest rows enter one by one and the rest through 40 power sums, all computed in SQL.
  - Rows heavy enough to be certain are drawn early and count exactly, through the weight they take away.
- **Checked.** In the unit tests, each score's mean, given the rows before it, is what the true share implies (and not what a share 5 points off implies). The run and the power sums read back from Postgres match those computed from the rows themselves.

Here is the CFPB join of #26, with Laya answering, labelled down one ranking by weight. Of the first 100 texts, 40 are heavy enough to be certain and 60 were drawn. Only 4 of the 60 are about debt collection, short of the 5 the usual interval needs. These are the library's own CLI tables, replayed on the stored answers. Before:

```
texts: share of judged rows whose true answer is 'debt collection', each counted by (SELECT count(*) FROM complaints c WHERE c.text_hash = s.text_hash) (95 % intervals)
group                   rows  labels     drawn  exact  model says  estimate      95 % interval     labels for ±2.0 pts
all rows               2,000     100 by weight   75 %      10.8 %     1.6 %          0.0 – 3.2                     +33  ⚠
⚠ fewer than 5 rows drawn by weight with or without the answer (rows certain to be drawn aside): the interval may be too narrow
```

Now:

```
group                   rows  labels     drawn  exact  model says  estimate      95 % interval     labels for ±2.0 pts
all rows               2,000     100 by weight   75 %      10.8 %     1.6 %          0.3 – 3.6                      +0  (betting)
(betting): fewer than 5 rows drawn by weight with or without the answer, so the interval is by betting: valid at any number of labels, and wider than the usual one would be once there are enough
```

The true share is 1.57 %. The interval is within ± 2 points already, so the 33 more labels the floor asked for aren't needed.

**Labels drawn at random, with weights.** Each label is scored w·(y − λ·f), against what the true share implies for the rows not yet drawn, and scaled by the heaviest of them. It is valid, but a heavy row not yet labelled makes it wide: random labels can't rule out that row having the answer. The table points to drawing by weight, as before.

**`--with`.** Below the floor, the interval is #30's, from the labels and the first version, in the order given, that has judged every row in scope. That is normally the active one; with none, the labels alone. The version is fixed before any label is looked at, so choosing it costs nothing. A version judging only a sample plays no part until the floor, as before. With `--with --weight`, the labels and the active version get the interval for labels drawn by weight.

Here is student loans by region with Laya active, 60 labels per region, and Jev 1.14 on 200 rows per region:

```
complaints: share of rows whose true answer is 'student loan', by region, from labels and 2 versions (95 % intervals); 1,992 rows
group                     rows  labels  v1 says   v2 rows  v2 says  estimate    95 % interval  labels alone
Midwest                    252      60    2.8 %       200    5.5 %     1.7 %        0.4 – 9.9     0.0 – 5.6  (betting)
Northeast                  336      60    0.6 %       200    1.0 %     0.0 %        0.0 – 6.8             –  (betting)
South                      984      60    1.5 %       200    3.0 %     5.0 %       0.3 – 10.9    0.0 – 10.9  (betting)
West                       420      60    0.7 %       200    2.0 %     0.0 %        0.0 – 7.1             –  (betting)
(betting): fewer than 10 labelled rows with or without the answer, so no version can be weighed against them yet and the interval is by betting from the labels and v1, which judged every row: valid at any number of labels, and wider than the usual one would be once there are enough
```

Every interval holds its region's share: 2.8 %, 1.2 %, 1.8 % and 1.7 %. For ± 3 points the plan asks for 150–200 labels per region.

### On real answers

`jevbench rarevalidate` ([report](../../results/rare-validation/report.md)) runs the following with no API calls.

**Does it hold?** On the CFPB join, with labels drawn by weight: the first 50, 100, 150 and 250 texts of a fresh ranking, 500 times each, for three answers judged by three models.
- **The betting interval held 96.2–99.8 %** in all 36 cells.
- **The usual interval below its floor held 63–93 %.** That counts the 21 cells with at least 50 such draws. Debt collection at 150 labels was the worst, at 63–64 %.
- **What pgdocket now reports held 95.0 % or more.** That is the usual interval above the floor and betting below it.
- **With labels drawn at random,** the flagged usual interval held 49–100 %, and betting held every time, at ± 30–45 points.

**Does it save labels?** In rounds, with each run starting from the same ranking or draws both ways, so the two differ only in the plan and the interval. **In no run did betting take more labels.**

| estimate | target | answer | fewer labels | runs with fewer |
| --- | --- | --- | ---: | ---: |
| `--with 2 --by region` (#28) | ± 3 points in every region | student loan (1.2–2.8 %) | 57–61 % | 100 % |
| | | mortgage (3.2–8.1 %) | 4–8 % | 42–66 % |
| | | credit card (5.5–9.2 %) | none | 0–6 % |
| `--with 2 --weight` (#29) | ± 2 points | debt collection (1.6 %) | 22–28 % | 42–46 % |
| | | credit card (2.4 %) | 12–17 % | 18–20 % |
| | | credit reporting (94 %) | 0–1 % | 0–2 % |
| `--weight` (#26) | ± 2 points | credit card (2.4 %) | 4–16 % | 14–38 % |
| | | debt collection (1.6 %) | 1–2 % | 5–6 % |
| | | credit reporting (94 %) | 0–1 % | 1–2 % |

- **`--with` by region pays most.** No version could be weighed against fewer than 10 student-loan complaints, so every region needed about 10 ÷ its share in labels. That was 3,000–3,300 labels in all, cut to 1,300.
- **`--weight` alone pays least.** Its floor is 5, not 10, and at ± 2 points the usual interval meets the target soon after the floor.
- **Final intervals held 90–100 %**, with betting and without. The lowest, 90 %, is credit reporting under `--with --weight` with Laya, the same both ways.

**Found on the way: a plan asking more of betting than of the floor.** In the first by-region runs, betting took more labels than the floor in about half the runs for mortgage. When a region's pilot held no complaint about mortgage, its betting interval's estimate was 0. The plan took that as the share, so the floor asked for every row, and betting's route won at up to twice the floor's labels.
- **The fix.** The plan now takes the share from the usual estimate, never nearer 0 or 1 than 1 in (labels + 2), as `estimate --by` already did.
- **The test.** The same paired runs found no run where betting took more labels, in any of the four estimates.

## In SQL (#32)

`docket.estimate()` now gives the betting interval itself (migration 012), so a dashboard or an application that queries the function gets an interval that holds at any number of labels, as the CLI does. The CLI prints what the function returns.

```sql
SELECT answer, model_count, round(estimate) AS estimate, round(ci_low) AS ci_low, round(ci_high) AS ci_high, method
FROM docket.estimate('k8s_route');
```

On the live replay's 300 issues it returns the table above, interval for interval, with `method = 'betting'` for the seven rare SIGs. The whole call takes 79 ms.

- **The same interval, found faster.** `docket.betting_interval(y, f, model_total, rows, alpha)` does the arithmetic of the Python, in PL/pgSQL, which a role without superuser can install. Rather than testing every candidate share, it finds each end of the interval by bisection, a dozen candidates each.
- **Why bisection is exact here.** The capital betting that the share is higher never grows as the candidate share rises, and the capital betting that it is lower never shrinks. So the shares no bet rejects form an interval.
- **Checked** (`jevbench sqlbettingvalidate`, [report](../../results/sql-betting/report.md)). In 303 random cases (scopes of 40 rows to a million, 1 to 1,200 labels, answers on 0.2 % to 60 % of rows), the SQL interval was the Python one in every case; 7 were empty both ways. A test in CI does the same on 62 cases.
- **Fast.** It takes 15–90 ms for a rare answer with 1,500 to 10,000 labels.
- **Only labels drawn at random.** With `p_sampling` naming any other kind of label (`--include-manual`), the function keeps the normal interval, as the CLI does.

## How this relates to other work

- **Betting confidence intervals** (Waudby-Smith & Ramdas, 2024) estimate the mean of a bounded variable at any sample size. pgdocket uses their hedged capital process for sampling without replacement, with their predictable plug-in bets for confidence sequences, at most ¾ of the largest safe bet. Their aGRAPA bets, which the audits use (#23), gave intervals up to 63 % wider here.
- **Prediction-powered inference** (Angelopoulos et al., 2023) has a nonasymptotic variant built on those intervals. It bounds the rectifier from the labels and the model's mean from unlabelled data, splitting the error between them. In pgdocket the model judges every row, so only the labels carry error.
- **Anytime-valid PPI.** Kilian, Cortinovis & Caron (2025) build prediction-powered confidence sequences that are valid over time asymptotically, and Csillag, Struchiner & Goedert (2025) give prediction-powered e-values. pgdocket's interval is valid at any sample size, not only asymptotically, for the shares it estimates.
- **Weighted sampling without replacement.** Shekhar, Xu, Lipton, Liang & Ramdas (2023) build betting confidence sequences for a weighted mean by importance weighting, for any design that draws one item at a time, with side information as a control variate. pgdocket applies that to its own designs: drawn at random, and drawn by weight in rank order, whose draw probabilities it derives above.
- **Clopper–Pearson** on the labels alone is exact for a proportion and narrower than betting at very small samples. It can't use the model, though, and splitting the error between the two would have gained in some cells what it lost in others.

## Limits

- **Labels drawn at random only.** The guarantee assumes the labels are a random sample of the scope or of the group, as `label --sample`, `--random` and `--stratified` record them.
- **Random labels can't vouch for heavy rows.** With weights as uneven as a join's fan-out, betting from labels drawn at random is valid but wide (± 30–45 points on the CFPB join), since a heavy row not yet labelled may have the answer. Draw by weight.
- **Below the floor, `--with` bets with one version that judged every row.** A second version judging a sample plays no part there; with `--weight`, neither does the second version's run.
- **The weighted plan judges betting's labels on a typical draw** from the rows not yet certain. It treats them as drawn at random, which the rows near certainty make a little optimistic. A short round is then planned again from what it shows.
- **In SQL (#32), only `docket.estimate()`.** It gives the betting interval itself, `method = 'betting'` and `reliable = true`, from labels drawn at random only; the CLI prints what it returns. `--by`, `--weight` and `--with` remain in the CLI and Python.
- **Wider than the normal interval would be.** With very few labels the betting interval is wide: ± 5–10 points at 50 labels for the answers above.
- **An empty set is possible.** With probability at most 5 %, every share is rejected; then nothing is reported, and the normal interval is shown with ⚠.
- **The floor itself is unchanged.** Above it, the normal interval stays, as validated in #14 and #25.

## Reproduce

```bash
docker compose run --rm bench bettingvalidate        # needs the stored check runs, the Phase 0 and Laya runs, and the source shard
docker compose run --rm bench rarevalidate           # #31: weighted shares and --with; the Phase 0 and Laya runs and the source shard
```
