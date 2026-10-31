# Benchmark run `laya-20261030-103603`

- Backend: `laya` · model `laya` · endpoint `http://laya:8000/v1/systemone` · 1 row(s) per request
- Rows: 2000 CFPB complaint narratives · started 2026-10-30 10:36:03.268186+00:00 · finished 2026-10-30 11:43:52.930282+00:00
- Ground truth: consumer-selected Product / Issue (noisy; agreement is a lower bound on fidelity)

## Headline numbers

- **Product choice (9 options):** accuracy 64.4 % [62.3 %, 66.4 %], top-label ECE 0.186 [0.168, 0.206] → 0.055 after temperature scaling (T = 1.78, zeros floored at 4.9985e-05) and 0.018 after isotonic (both cross-fitted).
- **Probability resolution:** smallest non-zero probability 9.997e-05; 1,338 exact zeros; the labeled class gets exactly 0 on 7 rows. Unfloored temperature scaling fits T = 1.86 and moves ECE to 0.055.
- **Option order:** 14.2 % [12.7 %, 15.7 %] of rows change their answer across 3 cyclic orderings. Listing an option first lifts its probability by -1.0 pp [-1.3, -0.8]; within-class recall changes by -2.8 pp [-4.0, -1.5] when the true class is listed first. Averaging orderings: accuracy 64.4 % → 66.0 % at 3× cost.
- **Label-set drift (adding "other financial product or service"):** 12.1 % of top-1 answers change (6.4 % move to the new option); at τ = 0.7 the auto-accepted set goes 1510 → 1407 rows (Jaccard 0.82). Keeping the same volume needs τ ≈ 0.64.
- **noul_debt_collection:** AUROC 0.672 [0.633, 0.710], ECE 0.217 [0.202, 0.231] → 0.017 isotonic / 0.025 Platt (cross-fitted).
- **noul_incorrect_report:** AUROC 0.635 [0.608, 0.661], ECE 0.105 [0.089, 0.125] → 0.016 isotonic / 0.018 Platt (cross-fitted).

## Sample

| product | rows |
| --- | ---: |
| credit reporting | 1248 |
| debt collection | 239 |
| credit card | 142 |
| checking or savings account | 124 |
| mortgage | 110 |
| money transfer or virtual currency | 39 |
| vehicle loan or lease | 38 |
| student loan | 36 |
| payday, title, or personal loan | 24 |

## Cost and latency

| experiment | seconds | requests | input tokens | est. cost (USD) |
| --- | ---: | ---: | ---: | ---: |
| choice_base | 672.2 | 2000 | 530,076 | none (free backend) |
| choice_other | 696.4 | 2000 | 541,918 | none (free backend) |
| choice_shift3 | 671.6 | 2000 | 530,076 | none (free backend) |
| choice_shift6 | 688.8 | 2000 | 530,076 | none (free backend) |
| noul_debt_collection | 670.6 | 2000 | 524,148 | none (free backend) |
| noul_incorrect_report | 669.5 | 2000 | 518,219 | none (free backend) |

## `choice_base`

Question: *Which financial product is this consumer complaint about?*

Options (in order): credit reporting, debt collection, mortgage, credit card, checking or savings account, student loan, money transfer or virtual currency, vehicle loan or lease, payday, title, or personal loan

| metric | value |
| --- | ---: |
| n | 2000 |
| accuracy | 64.4 % [62.3 %, 66.4 %] |
| macro-F1 | 0.505 |
| mean confidence | 0.828 |
| ECE (15 equal-width bins) | 0.186 [0.168, 0.206] |
| ECE (15 equal-mass bins) | 0.184 |
| Brier / NLL | 0.551 / 1.367 |
| smallest non-zero probability / exact zeros | 9.997e-05 / 1,338 |
| rows where the labeled class has probability 0 | 7 |
| ECE after temperature (T=1.78, floor 4.9985e-05) | 0.055 |
| ECE after temperature, unfloored (T=1.86) | 0.055 |
| ECE after isotonic | 0.018 |
| corr(reported confidence, max prob) | 0.963 |

| option | support | predicted | recall | precision |
| --- | ---: | ---: | ---: | ---: |
| credit reporting | 1248 | 878 | 65.7 % | 93.4 % |
| debt collection | 239 | 520 | 73.2 % | 33.7 % |
| mortgage | 110 | 125 | 82.7 % | 72.8 % |
| credit card | 142 | 318 | 72.5 % | 32.4 % |
| checking or savings account | 124 | 76 | 41.1 % | 67.1 % |
| student loan | 36 | 27 | 47.2 % | 63.0 % |
| money transfer or virtual currency | 39 | 28 | 38.5 % | 53.6 % |
| vehicle loan or lease | 38 | 23 | 36.8 % | 60.9 % |
| payday, title, or personal loan | 24 | 5 | 8.3 % | 40.0 % |

Reliability (raw):

| bin | n | mean confidence | observed accuracy | gap |
| --- | ---: | ---: | ---: | ---: |
| 0.2–0.3 | 14 | 0.269 | 0.429 | +0.160 |
| 0.3–0.4 | 57 | 0.365 | 0.281 | -0.084 |
| 0.4–0.5 | 109 | 0.462 | 0.349 | -0.113 |
| 0.5–0.6 | 160 | 0.548 | 0.469 | -0.079 |
| 0.6–0.7 | 150 | 0.656 | 0.487 | -0.169 |
| 0.7–0.8 | 196 | 0.754 | 0.526 | -0.228 |
| 0.8–0.9 | 255 | 0.851 | 0.588 | -0.263 |
| 0.9–1.0 | 1059 | 0.973 | 0.781 | -0.192 |

## `choice_other`

Question: *Which financial product is this consumer complaint about?*

Options (in order): credit reporting, debt collection, mortgage, credit card, checking or savings account, student loan, money transfer or virtual currency, vehicle loan or lease, payday, title, or personal loan, other financial product or service

| metric | value |
| --- | ---: |
| n | 2000 |
| accuracy | 63.0 % [60.7 %, 65.2 %] |
| macro-F1 | 0.497 |
| mean confidence | 0.805 |
| ECE (15 equal-width bins) | 0.174 [0.159, 0.195] |
| ECE (15 equal-mass bins) | 0.174 |
| Brier / NLL | 0.561 / 1.422 |
| smallest non-zero probability / exact zeros | 9.997e-05 / 1,441 |
| rows where the labeled class has probability 0 | 6 |
| ECE after temperature (T=1.72, floor 4.9985e-05) | 0.047 |
| ECE after temperature, unfloored (T=1.79) | 0.048 |
| ECE after isotonic | 0.023 |
| corr(reported confidence, max prob) | 0.968 |

| option | support | predicted | recall | precision |
| --- | ---: | ---: | ---: | ---: |
| credit reporting | 1248 | 882 | 66.3 % | 93.8 % |
| debt collection | 239 | 469 | 70.3 % | 35.8 % |
| mortgage | 110 | 117 | 78.2 % | 73.5 % |
| credit card | 142 | 265 | 62.0 % | 33.2 % |
| checking or savings account | 124 | 71 | 39.5 % | 69.0 % |
| student loan | 36 | 28 | 44.4 % | 57.1 % |
| money transfer or virtual currency | 39 | 16 | 30.8 % | 75.0 % |
| vehicle loan or lease | 38 | 21 | 34.2 % | 61.9 % |
| payday, title, or personal loan | 24 | 3 | 8.3 % | 66.7 % |
| other financial product or service | 0 | 128 | – | 0.0 % |

Reliability (raw):

| bin | n | mean confidence | observed accuracy | gap |
| --- | ---: | ---: | ---: | ---: |
| 0.2–0.3 | 16 | 0.279 | 0.188 | -0.091 |
| 0.3–0.4 | 93 | 0.356 | 0.301 | -0.055 |
| 0.4–0.5 | 137 | 0.451 | 0.387 | -0.064 |
| 0.5–0.6 | 171 | 0.549 | 0.415 | -0.134 |
| 0.6–0.7 | 176 | 0.649 | 0.409 | -0.240 |
| 0.7–0.8 | 169 | 0.751 | 0.550 | -0.200 |
| 0.8–0.9 | 240 | 0.852 | 0.637 | -0.215 |
| 0.9–1.0 | 998 | 0.972 | 0.790 | -0.183 |

## `choice_shift3`

Question: *Which financial product is this consumer complaint about?*

Options (in order): credit card, checking or savings account, student loan, money transfer or virtual currency, vehicle loan or lease, payday, title, or personal loan, credit reporting, debt collection, mortgage

| metric | value |
| --- | ---: |
| n | 2000 |
| accuracy | 65.4 % [63.2 %, 67.4 %] |
| macro-F1 | 0.494 |
| mean confidence | 0.826 |
| ECE (15 equal-width bins) | 0.174 [0.159, 0.195] |
| ECE (15 equal-mass bins) | 0.172 |
| Brier / NLL | 0.531 / 1.296 |
| smallest non-zero probability / exact zeros | 9.997e-05 / 1,277 |
| rows where the labeled class has probability 0 | 5 |
| ECE after temperature (T=1.73, floor 4.9985e-05) | 0.060 |
| ECE after temperature, unfloored (T=1.78) | 0.060 |
| ECE after isotonic | 0.043 |
| corr(reported confidence, max prob) | 0.964 |

| option | support | predicted | recall | precision |
| --- | ---: | ---: | ---: | ---: |
| credit card | 142 | 296 | 69.7 % | 33.4 % |
| checking or savings account | 124 | 75 | 40.3 % | 66.7 % |
| student loan | 36 | 29 | 47.2 % | 58.6 % |
| money transfer or virtual currency | 39 | 21 | 33.3 % | 61.9 % |
| vehicle loan or lease | 38 | 25 | 39.5 % | 60.0 % |
| payday, title, or personal loan | 24 | 3 | 4.2 % | 33.3 % |
| credit reporting | 1248 | 913 | 68.3 % | 93.3 % |
| debt collection | 239 | 514 | 73.2 % | 34.0 % |
| mortgage | 110 | 124 | 78.2 % | 69.4 % |

Reliability (raw):

| bin | n | mean confidence | observed accuracy | gap |
| --- | ---: | ---: | ---: | ---: |
| 0.1–0.2 | 1 | 0.178 | 0.000 | -0.178 |
| 0.2–0.3 | 12 | 0.271 | 0.167 | -0.104 |
| 0.3–0.4 | 64 | 0.353 | 0.359 | +0.007 |
| 0.4–0.5 | 120 | 0.458 | 0.317 | -0.141 |
| 0.5–0.6 | 149 | 0.548 | 0.477 | -0.071 |
| 0.6–0.7 | 148 | 0.652 | 0.507 | -0.145 |
| 0.7–0.8 | 199 | 0.754 | 0.568 | -0.186 |
| 0.8–0.9 | 248 | 0.853 | 0.609 | -0.244 |
| 0.9–1.0 | 1059 | 0.975 | 0.788 | -0.186 |

## `choice_shift6`

Question: *Which financial product is this consumer complaint about?*

Options (in order): money transfer or virtual currency, vehicle loan or lease, payday, title, or personal loan, credit reporting, debt collection, mortgage, credit card, checking or savings account, student loan

| metric | value |
| --- | ---: |
| n | 2000 |
| accuracy | 66.0 % [63.9 %, 68.0 %] |
| macro-F1 | 0.504 |
| mean confidence | 0.842 |
| ECE (15 equal-width bins) | 0.184 [0.167, 0.204] |
| ECE (15 equal-mass bins) | 0.182 |
| Brier / NLL | 0.536 / 1.343 |
| smallest non-zero probability / exact zeros | 9.997e-05 / 1,622 |
| rows where the labeled class has probability 0 | 7 |
| ECE after temperature (T=1.80, floor 4.9985e-05) | 0.052 |
| ECE after temperature, unfloored (T=1.89) | 0.054 |
| ECE after isotonic | 0.020 |
| corr(reported confidence, max prob) | 0.965 |

| option | support | predicted | recall | precision |
| --- | ---: | ---: | ---: | ---: |
| money transfer or virtual currency | 39 | 25 | 35.9 % | 56.0 % |
| vehicle loan or lease | 38 | 27 | 44.7 % | 63.0 % |
| payday, title, or personal loan | 24 | 2 | 4.2 % | 50.0 % |
| credit reporting | 1248 | 930 | 69.2 % | 92.8 % |
| debt collection | 239 | 481 | 72.0 % | 35.8 % |
| mortgage | 110 | 122 | 79.1 % | 71.3 % |
| credit card | 142 | 316 | 70.4 % | 31.6 % |
| checking or savings account | 124 | 68 | 37.9 % | 69.1 % |
| student loan | 36 | 29 | 50.0 % | 62.1 % |

Reliability (raw):

| bin | n | mean confidence | observed accuracy | gap |
| --- | ---: | ---: | ---: | ---: |
| 0.2–0.3 | 14 | 0.280 | 0.357 | +0.077 |
| 0.3–0.4 | 47 | 0.355 | 0.298 | -0.057 |
| 0.4–0.5 | 97 | 0.460 | 0.412 | -0.047 |
| 0.5–0.6 | 132 | 0.547 | 0.402 | -0.146 |
| 0.6–0.7 | 155 | 0.652 | 0.497 | -0.155 |
| 0.7–0.8 | 175 | 0.752 | 0.503 | -0.249 |
| 0.8–0.9 | 256 | 0.851 | 0.645 | -0.207 |
| 0.9–1.0 | 1124 | 0.975 | 0.780 | -0.195 |

## `noul_debt_collection`

Question: *the complaint is about a debt collector or about collecting a debt*

| metric | value |
| --- | ---: |
| n / prevalence | 2000 / 11.9 % |
| AUROC | 0.672 [0.633, 0.710] |
| ECE (15 equal-width bins) | 0.217 [0.202, 0.231] |
| ECE (15 equal-mass bins) | 0.217 |
| Brier / NLL | 0.164 / 0.517 |
| precision / recall / F1 at 0.5 | 0.251 / 0.469 / 0.327 |
| best F1 (threshold) | 0.333 (0.59) |
| ECE / Brier after isotonic | 0.017 / 0.100 |
| ECE / Brier after Platt | 0.025 / 0.100 |

Reliability (raw):

| bin | n | mean confidence | observed accuracy | gap |
| --- | ---: | ---: | ---: | ---: |
| 0.0–0.1 | 87 | 0.084 | 0.092 | +0.008 |
| 0.1–0.2 | 585 | 0.152 | 0.065 | -0.087 |
| 0.2–0.3 | 456 | 0.246 | 0.088 | -0.158 |
| 0.3–0.4 | 242 | 0.343 | 0.079 | -0.265 |
| 0.4–0.5 | 183 | 0.450 | 0.120 | -0.330 |
| 0.5–0.6 | 143 | 0.546 | 0.175 | -0.371 |
| 0.6–0.7 | 139 | 0.646 | 0.245 | -0.402 |
| 0.7–0.8 | 105 | 0.751 | 0.267 | -0.485 |
| 0.8–0.9 | 51 | 0.832 | 0.353 | -0.479 |
| 0.9–1.0 | 9 | 0.925 | 0.778 | -0.147 |

## `noul_incorrect_report`

Question: *the consumer says their credit report contains incorrect information*

| metric | value |
| --- | ---: |
| n / prevalence | 2000 / 28.1 % |
| AUROC | 0.635 [0.608, 0.661] |
| ECE (15 equal-width bins) | 0.105 [0.089, 0.125] |
| ECE (15 equal-mass bins) | 0.103 |
| Brier / NLL | 0.209 / 0.626 |
| precision / recall / F1 at 0.5 | 0.444 / 0.278 / 0.342 |
| best F1 (threshold) | 0.474 (0.12) |
| ECE / Brier after isotonic | 0.016 / 0.195 |
| ECE / Brier after Platt | 0.018 / 0.195 |

Reliability (raw):

| bin | n | mean confidence | observed accuracy | gap |
| --- | ---: | ---: | ---: | ---: |
| 0.0–0.1 | 350 | 0.076 | 0.131 | +0.055 |
| 0.1–0.2 | 711 | 0.142 | 0.249 | +0.107 |
| 0.2–0.3 | 301 | 0.243 | 0.292 | +0.050 |
| 0.3–0.4 | 156 | 0.344 | 0.308 | -0.037 |
| 0.4–0.5 | 131 | 0.453 | 0.359 | -0.094 |
| 0.5–0.6 | 142 | 0.548 | 0.472 | -0.076 |
| 0.6–0.7 | 92 | 0.649 | 0.413 | -0.236 |
| 0.7–0.8 | 75 | 0.747 | 0.400 | -0.347 |
| 0.8–0.9 | 33 | 0.835 | 0.485 | -0.350 |
| 0.9–1.0 | 9 | 0.942 | 0.556 | -0.387 |

## Option-order sensitivity

Orderings: choice_base, choice_shift3, choice_shift6 (cyclic shifts of the same options), 2000 rows.

| metric | value |
| --- | ---: |
| rows whose answer changes with order | 14.2 % [12.7 %, 15.7 %] |
| answers that are the first-listed option | 20.0 % |
| truths that are the first-listed option | 23.8 % |
| excess first-position answers | -3.8 % [-4.6 %, -3.1 %] |
| probability lift when listed first | -1.02 pp |
| within-class recall gain when the true class is listed first | -2.80 pp [-3.99, -1.47] |
| accuracy: canonical order → averaged over orderings | 64.4 % → 66.0 % (Δ CI [0.7 %, 2.5 %]) |
| ECE: canonical order → averaged | 0.186 → 0.164 |

| class listed first | rows | recall when first | recall in other orderings |
| --- | ---: | ---: | ---: |
| credit reporting | 1248 | 65.7 % | 68.7 % |
| credit card | 142 | 69.7 % | 71.5 % |
| money transfer or virtual currency | 39 | 35.9 % | 35.9 % |

## Label-set drift

Same question and order, with "other financial product or service" appended. 2000 rows.

| metric | value |
| --- | ---: |
| top-1 answers that change | 12.1 % [10.7 %, 13.6 %] |
| answers moving to the new option | 6.4 % |
| mean Δ p(true label) | -0.0202 |
| mean Δ max probability | -0.0237 |
| accuracy before → after | 64.4 % → 63.0 % |

| τ | auto-accepted before | after | Jaccard | precision before | after |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 0.5 | 1820 | 1754 | 0.89 | 67.5 % | 67.1 % |
| 0.7 | 1510 | 1407 | 0.82 | 71.5 % | 73.5 % |
| 0.9 | 1059 | 998 | 0.81 | 78.1 % | 79.0 % |

## Method notes

- ECE is the count-weighted |accuracy − confidence| over bins (top-label confidence for choice). Brackets are 95 % percentile-bootstrap intervals over rows (1,000 resamples).
- Recalibrated numbers are **out-of-fold** (5-fold cross-fitting), so they are not scored on the rows they were fitted on.
- If a backend reports exact zeros, temperature scaling is fitted after flooring probabilities at half the smallest non-zero value; otherwise a handful of zero-probability labels dominate NLL and the fitted temperature.
- Yes/no truth is a coded field (product or issue). A confident "yes" on a row coded otherwise can be a model error **or** a narrative that satisfies the question while the consumer filed it under another issue; raw ECE mixes both. Recalibrated ECE measures how well the scores map onto *this* label definition.
- Sampling: CC0 mirror of CFPB narratives; the source, revision, seed and truncation are recorded in `data/raw/*.manifest.json` (default: uniform sample of parquet shard 0, de-duplicated by narrative, ≤ 1,500 chars).
- Each experiment runs in a fresh session with `jev_cache_clear()`: pg-jev's answer cache key omits `jev.model` and `jev.api_url`.
