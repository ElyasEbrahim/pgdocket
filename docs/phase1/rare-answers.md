# Rare answers: intervals that need no floor

*2026-11-05 · issue #30 · lineage: estimating means by betting (Waudby-Smith & Ramdas, 2024), prediction-powered inference's nonasymptotic variant (Angelopoulos et al., 2023) · evidence: [results/betting-validation](../../results/betting-validation/report.md)*

PPI++'s interval, estimate ± z·se, rests on the central limit theorem. It holds once the labels contain enough rows with and without the answer, so pgdocket waits for 10 of each before trusting one. For a rare answer that floor, not the target, sets how many labels are needed:
- an answer on 1.8 % of rows needs about 550 labels before the interval can be trusted, whatever precision was asked for;
- below the floor the normal interval is often wrong. On real answers at 50–200 labels it held the truth in 28–98 % of draws (median 87 %), not 95 %.

## What pgdocket does

Below the floor, `estimate` and `estimate --by` give a **betting interval** instead, marked `(betting)`. Here it is on the 300 Kubernetes issues of the live replay, judged by Jev, with 80 labels drawn at random:

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

## How this relates to other work

- **Betting confidence intervals** (Waudby-Smith & Ramdas, 2024) estimate the mean of a bounded variable at any sample size. pgdocket uses their hedged capital process for sampling without replacement, with their predictable plug-in bets for confidence sequences, at most ¾ of the largest safe bet. Their aGRAPA bets, which the audits use (#23), gave intervals up to 63 % wider here.
- **Prediction-powered inference** (Angelopoulos et al., 2023) has a nonasymptotic variant built on those intervals. It bounds the rectifier from the labels and the model's mean from unlabelled data, splitting the error between them. In pgdocket the model judges every row, so only the labels carry error.
- **Anytime-valid PPI.** Kilian, Cortinovis & Caron (2025) build prediction-powered confidence sequences that are valid over time asymptotically, and Csillag, Struchiner & Goedert (2025) give prediction-powered e-values. pgdocket's interval is valid at any sample size, not only asymptotically, for the shares it estimates.
- **Clopper–Pearson** on the labels alone is exact for a proportion and narrower than betting at very small samples. It can't use the model, though, and splitting the error between the two would have gained in some cells what it lost in others.

## Limits

- **Labels drawn at random only.** The guarantee assumes the labels are a random sample of the scope or of the group, as `label --sample`, `--random` and `--stratified` record them.
- **Weighted shares and several models keep their floors for now.** That is `--weight`, `--with`, and `--with --weight`.
- **The SQL function `docket.estimate()` is unchanged.** It still gives the normal interval with `reliable = false`; the CLI replaces it.
- **Wider than the normal interval would be.** With very few labels the betting interval is wide: ± 5–10 points at 50 labels for the answers above.
- **An empty set is possible.** With probability at most 5 %, every share is rejected; then nothing is reported, and the normal interval is shown with ⚠.
- **The floor itself is unchanged.** Above it, the normal interval stays, as validated in #14 and #25.

## Reproduce

```bash
docker compose run --rm bench bettingvalidate        # needs the stored check runs, the Phase 0 and Laya runs, and the source shard
```
