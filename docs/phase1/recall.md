# Recall targets for yes/no decisions

*2026-10-25 · issue #21 · design: [ADR 0003](../adr/0003-human-labels-are-the-oracle.md) · lineage: SUPG (Kang et al., 2020), BARGAIN (Zeighami et al., SIGMOD 2026)*

A yes/no decision ("is this ticket urgent?", "is this a feature request?") has two ways to fail:
- **Saying yes wrongly.** The precision threshold already covers this: answers above it are *yes* with precision ≥ the target.
- **Missing a real yes.** Teams usually care at least as much about this side: "make sure we miss at most 10 % of the urgent tickets".

A recall target covers the second. Below a threshold, rows are answered *no* without review, and at least the target share of the real yeses stays above it. The precision threshold and the recall threshold together give three bands:

| model's score P(yes) | answer | `auto` | guarantee |
| --- | --- | --- | --- |
| ≥ precision threshold | yes | true | at least the precision target of these are real yeses |
| between the two | best guess | **false**: a person reviews it | none |
| < recall threshold | no | true | together, these rows hold at most (1 − recall target) of the real yeses |

All guarantees are at 95 % confidence.

```bash
pgdocket calibrate urgent --precision-target 0.9 --recall-target 0.9
# urgent: 300 labels · … · auto when the model's score ≥ 0.910: 18.0 % of labelled rows, precision ≥ 90 % (95 % confidence)
#   auto 'no' when P(yes) < 0.100: 62.0 % of labelled rows, keeping ≥ 90 % of true yeses (95 % confidence; 64 labelled yeses)
```

The output above is illustrative.

## How it works

- **Candidates.** The candidate thresholds are the distinct scores, which don't depend on the labels.
- **Testing.** They're tested from the lowest upward; at the lowest, every yes is kept. At each candidate, a one-sided Clopper–Pearson lower bound on recall is computed over the labelled yeses. Testing stops at the first threshold whose bound falls below the target.
- **Why no correction is needed.** Recall can only fall as the threshold rises, so this fixed-sequence procedure keeps the chance of any false claim at 5 % without a multiple-testing correction.
- **Why the labels must be random.** When labels are drawn at random, the labelled yeses are a random sample of all yeses.
- **Too few yeses.** Even if the model keeps every labelled yes, n yeses can only prove recall up to 0.05^(1/n). That takes 14 yeses for 80 %, 29 for 90 % and 59 for 95 %. When there are too few, `calibrate` says how many it needs (BARGAIN shows that with few positives no method can guarantee recall and still answer many rows automatically). Rare yeses therefore need many labels: at 12 % prevalence, 29 yeses take about 240 random labels.
- **Three places use it.** The view applies the bands (migration 006), `promote` keeps the recall target when it recalibrates a new version, and `check` reports what each recall target would allow.

## Evidence

**Simulation (in CI).** On synthetic populations, 400 trials with 300 labels and a 90 % target: true recall fell short in 3.0 % of trials (5 % allowed). On average, 57 % of rows were answered *no* automatically.

**Real model errors, no API calls** ([results/recall-validation](../../results/recall-validation/report.md), `jevbench recallvalidate`). There were three yes/no tasks with stored Jev answers and known labels, and each set of judged rows stands in for the population. Results with 400 random labels (1,000 draws each):

| Task (share of real yeses) | keep ≥ 90 % of yeses: answered *no* automatically | if every label were known | failures |
| --- | ---: | ---: | ---: |
| Kubernetes: is it a feature request? (21.6 %) | 62.8 % | 75.4 % | 2.4 % |
| CFPB: is it about an incorrect credit report? (28.1 %) | 30.4 % | 37.6 % | 3.0 % |
| CFPB: is it about debt collection? (11.9 %) | 24.0 % | 52.8 % | 2.5 % |

- **The guarantee held everywhere.** Across all 27 settings (3 tasks × 3 targets × 3 label budgets), at most 3.6 % of trials fell short; 5 % is allowed.
- **It saves real work.** On the Kubernetes question, keeping 90 % of feature requests lets nearly two thirds of issues skip review with 400 labels.
- **Rare yeses need labels.** Debt collection is 12 % of complaints, so 200 labels found a 90 % threshold in only 15 % of draws; 400 labels found one 99.9 % of the time.
- **The full Kubernetes check:** with all 500 labels, the saved report (`results/check-k8s-feature`) allows 66.2 % at ≥ 90 %, 77.6 % at ≥ 80 % and 7.8 % at ≥ 95 %. A 95 % target requires keeping 106 of the 108 labelled yeses.

## Limits

- **Random labels only.** BARGAIN samples labels adaptively, only above candidate thresholds, and needs fewer. That would need labels that record their inclusion probability (ADR 0003).
- **Yes/no decisions only.** For a choice decision, "recall of one option" could be added the same way.
- **The guarantee is about the rows judged so far,** like every guarantee here. It assumes new rows come from the same distribution.
