# Another backend: Laya on your own machine

*2026-10-30 · issue #4 · backend: [Laya](https://huggingface.co/convaiinnovations/laya) 0.4.16 (Convai Innovations; open weights, Apache-2.0) · evidence: [the comparison](../../results/backends/report.md), Laya's `check` reports ([routing](../../results/check-laya-k8s-sig/report.md), [feature requests](../../results/check-laya-k8s-feature/report.md), [CFPB](../../results/check-laya-cfpb-product/report.md)), [Laya's Phase 0 run](../../results/laya-20261030-103603/report.md)*

pgdocket calls any server that speaks TypeSafe's `/v1/systemone` API. Laya is one you can run yourself: open weights, a CPU is enough, there is no per-call price and no row leaves the machine. Issue #4 asked for the first real test of an idea at the heart of pgdocket. Thresholds and calibration are computed from your own labels, so they should mean the same whichever model produced the answers. We judged the same labelled rows with Jev and with Laya and changed nothing else. An earlier comparison of the two reviewed their published specifications ([dos Santos Barbosa, 2026](https://arxiv.org/abs/2609.28940)); this one measures them.

## How it was run

- **Laya.** `docker compose --profile laya up -d laya` builds a CPU-only image (1.7 GB) and downloads three checkpoints (2.4 GB) into a Docker volume. `--model laya` pins Laya's English checkpoint (ModernBERT-large, 421M parameters, reads 512 tokens); a Jev model name lets Laya's router pick a checkpoint by language instead. It ran on 12 CPU cores of an Apple M3 Max, in Docker, without a GPU.
- **`pgdocket check`** on three tasks, with the same 500-row sample, seed, question, options, labels and consistency tests as the Jev reports: Kubernetes issues routed to one of 10 SIGs; Kubernetes issues, "is this a feature request?"; CFPB complaints by product (9 options).
- **The six Phase 0 experiments** (`jevbench run`) on the 2,000 CFPB complaints, through pg-jev, one row per request.

## Results

| | Kubernetes SIG, Jev | Kubernetes SIG, Laya | feature request?, Jev | feature request?, Laya | CFPB product, Jev | CFPB product, Laya |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| agreement with the labels | 88.8 % | 38.0 % | AUROC 0.966 | AUROC 0.804 | 79.8 % | 65.0 % |
| calibration error, raw → calibrated on the labels | 0.073 → 0.029 | 0.193 → 0.179 | 0.037 → 0.013 | 0.365 → 0.093 | 0.133 → 0.037 | 0.189 → 0.074 |
| `auto` at ≥ 90 % precision, one threshold | 90.0 % | 0 | 0 | 0 | 0 | 0 |
| best guarantee below that | – | none ≥ 70 % | 20 % of rows at 80 % | 5 % at 70 % | 85.6 % at 80 % | none ≥ 70 % |
| `auto` at ≥ 90 %, each answer on its own threshold | 48.2 % | 0 | – | – | 51.2 % | 36.4 % |
| answers that change when asked again | 1 % | 0 % | 1 % | 0 % | not tested | not tested |
| answers that change at 20 rows per request | 15 % | 76 % | 8 % | 53 % | not tested | not tested |
| seconds per judgment | 0.025 | 0.53 | 0.025 | 0.51 | 0.021 | 0.34 |
| cost per million rows | $43 | none | $35 | none | $24 | none |

Agreement and calibration come with confidence intervals in the [comparison](../../results/backends/report.md); for example, routing accuracy is 88.8 % [86.0, 91.4] for Jev and 38.0 % [34.0, 42.2] for Laya.

## What it means

1. **The guarantees held; what changed is how much they allow.** Zero-shot, Laya agreed with the labels much less often than Jev on all three tasks. pgdocket didn't need to know that in advance. The thresholds come from your labels, so on Kubernetes routing `calibrate` would certify nothing and send every issue to review: the right outcome for a model that is right 38 % of the time. Confident-looking scores can't buy a backend automation.
2. **Laya's confidence is the weak spot, not only its accuracy.** On routing, the 15 % of answers Laya was surest of were right 30.7 % of the time, below its 38 % overall. Calibration can fix what scores claim, not how they rank answers: temperature scaling moved the calibration error only from 0.193 to 0.179. On CFPB complaints, with 2,000 labels, it made Laya's probabilities as reliable as Jev's (Phase 0, below). On feature requests its ranking is useful (AUROC 0.80), and calibration on the labels cut the error from 0.365 to 0.093. It still says *yes* far too often, though: at 0.5, 28.5 % of its yeses were feature requests.
3. **Per-answer guarantees rescue what one threshold can't.** On CFPB, no single threshold reached even 70 % precision with Laya, because its surest answers mix products it knows with products it doesn't. Its *credit reporting* answers pass 90 % on a threshold of their own, so `calibrate --per-answer` would accept 36.4 % of complaints automatically (Jev: 51.2 %). `check` now says so, instead of advising more labels.
4. **Never send Laya more than one row per request.** In a request with several rows, Laya answers every question from the whole request. A storage issue and a networking issue sent together both came back "storage", in either order. pg-jev sends 20 rows per request by default, which on Laya gives the rows of a request nearly the same answer: of 20 issues sent together, 16 came back *testing*, against five different answers when judged one by one. Routing accuracy fell from 38 % to 12 %, about chance for 10 options. pgdocket judges one row per request, and `check --batch-test` catches this ("Judge one row per request. Sending 20 rows per request changed 76.0 % of answers"). An [issue for Laya](upstream-laya-shared-state.md) is drafted.
5. **Laya is deterministic.** None of the 200 rows `check` asked again changed (Jev: 2 of 200), and two full runs of 2,000 complaints gave identical probabilities on every row. Across three runs, Jev changed the top answer on 4.8 % of them. A re-judged answer from Laya is the same answer, and in the order test below every change comes from the order alone.
6. **No API cost, about 20× slower here.** laya-serve runs one inference at a time: 0.3–0.5 s per row on this CPU, so a million rows would take 4–6 days. Hosted Jev judged about 40 rows a second, at $24–43 per million rows.
7. **A fine-tuned Laya is the real candidate.** Laya's own README says its base checkpoints score near chance zero-shot on its typed-decisions benchmark, and that a checkpoint fine-tuned on that benchmark's training split reached 0.766 there. pgdocket's ledger already holds the labels a fine-tune needs, and `check` would show a fine-tuned model's guarantees before it serves anything. Not built.

## Phase 0: the six experiments on 2,000 CFPB complaints

The experiments issue #4 named, run through pg-jev with one row per request ([Laya's run](../../results/laya-20261030-103603/report.md), [the comparison](../../results/backends/report.md)). Laya's first run stopped after `choice_base` on a harness limit (now fixed). Running it again measured Laya's determinism on all 2,000 complaints.

| | Jev, 20 rows per request | Jev, 1 row per request | Laya, 1 row per request |
| --- | ---: | ---: | ---: |
| product accuracy | 77.0 % | 79.3 % | 64.4 % [62.3, 66.4] |
| calibration error, raw | 0.089 | 0.126 | 0.186 |
| …after temperature scaling / isotonic (cross-fitted on the labels) | 0.029 / 0.027 | 0.032 / 0.056 | 0.055 / 0.018 |
| answers that change when the run is repeated | 4.8 % (3 runs) | – | 0.0 % (2 runs) |
| answers that change across 3 orders of the options | 7.4 % | – | 14.2 % |
| probability of the option listed first | +1.0 points | – | −1.0 points |
| accuracy when the 3 orders are averaged (3× the calls) | 76.6 % | – | 66.0 % |
| answers that change when a catch-all option is added | 5.8 % | – | 12.1 % |
| yes/no AUROC: debt collection? · incorrect credit report? | 0.884 · 0.748 | – | 0.672 · 0.635 |
| wall time per row | 0.002 s | 0.030 s | 0.34 s |

- **Calibration on labels makes the two comparable, as #4 hoped.** With 2,000 labels, isotonic calibration brought Laya's error down to 0.018, as low as Jev's (0.027). On the yes/no questions both ended at 0.016–0.024. A calibrated 0.8 means about 80 % for either model. Calibration can't change how often a model is right, or how well its scores separate right from wrong answers. That is where Laya trails, so its calibrated answers clear a precision target far less often.
- **Laya is twice as sensitive to option order, and averaging helps it.** 14.2 % [12.7, 15.7] of its answers changed across three orders of the same options, against 7.4 % for Jev, about 5 points of which is Jev's run-to-run noise. Laya is deterministic, so all of its 14.2 % comes from the order. Unlike Jev, it slightly disfavours the option listed first. Averaging the three orders raised its accuracy by 1.6 points [0.7, 2.6], at three times the calls; for Jev it made no difference. Laya's README says its published checkpoints keep a "sequential" option layout. It describes an order-free layout for checkpoints trained with it, after Parallel Context Windows (Ratner et al., 2023).
- **Adding an option moves twice as many answers.** A catch-all "other financial product or service" changed 12.1 % of Laya's answers (6.4 % moved to it), against 5.8 % for Jev. pgdocket turns any change to the options into a new version, which `compare` measures before it replaces the old one.

## Found along the way

- **pgdocket sent TypeSafe's API key to any backend.** `TYPESAFE_API_KEY` went into the `Authorization` header of every request, whichever server it was for, and the harness gave it to pg-jev for any URL. A decision pointed at someone else's `/v1/systemone` server would have handed them the key. Both now send it only to TypeSafe's hosts. The Laya container that received it here runs on this machine, and its logs hold no headers.
- **`check` priced free backends at Jev's list price.** The first Laya report showed $0.0123. A backend you run yourself now shows no API cost.
- **`check` told a hopeless case to label more rows.** It now says when even the surest answers are demonstrably below the target, and points to per-answer guarantees when those work.

## Limits

- **Zero-shot only,** with Laya's English checkpoint as shipped. Fine-tuning on your own decisions is what Laya is built for.
- **One sample per task** (500 rows; 2,000 in Phase 0), on one machine. Timings depend on the hardware: a GPU, or Laya's own batch endpoint (one state per row), would be much faster.
- **Jev's six-experiment Phase 0 run sent 20 rows per request;** Laya's sent one, because 20 breaks it. Jev's one-row run covers `choice_base` only.
- **Laya changes fast.** These are version 0.4.16, October 2026.
- **Steering (#24) was not measured on Laya** in this round.

## Reproduce

```bash
docker compose --profile laya up -d --wait laya      # first start downloads the checkpoints (2.4 GB)
docker compose run --rm --entrypoint python bench -m pgdocket.cli check \
  --backend http://laya:8000/v1/systemone --model laya --sample 500 --seed 1 \
  --table demo.k8s_issues --key number --text title,body --kind choice \
  --question "Which Kubernetes SIG (special interest group) should handle this issue?" \
  --options-file examples/k8s_sig_options.json --label-column sig --repeat 100 --batch-test 100 \
  --out results/check-laya-k8s-sig
docker compose run --rm bench run --backend laya --api-url http://laya:8000/v1/systemone --model laya \
  --batch-size 1 --analyze                           # the six Phase 0 experiments
docker compose run --rm --no-deps bench backends     # the comparison, from the stored reports
docker compose --profile laya rm -sf laya && docker volume rm docket-phase0_laya-cache   # remove Laya and its weights
```
