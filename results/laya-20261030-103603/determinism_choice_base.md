# Run-to-run determinism: `choice_base`

Runs: `laya-20261030-103603`, `laya-20261030-101135` · 2000 rows · identical requests (same rows, order, batches, options and model).

| metric | value |
| --- | ---: |
| rows with identical probabilities in every run | 100.0 % |
| rows with the same top answer in every run | 100.0 % |
| rows where some option moves by ≥ 0.05 | 0.0 % |
| largest probability change seen | 0.00 |
| mean confidence (first run): rows whose answer flips / stable rows | – / 0.828 |

| run pair | top-1 agreement | identical probabilities | rows with max change ≥ 0.05 | mean max change |
| --- | ---: | ---: | ---: | ---: |
| `laya-20261030-103603` vs `laya-20261030-101135` | 100.0 % | 100.0 % | 0.0 % | 0.0000 |

| run | accuracy | ECE |
| --- | ---: | ---: |
| `laya-20261030-103603` | 64.4 % | 0.186 |
| `laya-20261030-101135` | 64.4 % | 0.186 |

Largest change of any option probability per row, across runs:

| change | rows |
| --- | ---: |
| 0.00-0.01 | 2000 |
| 0.01-0.02 | 0 |
| 0.02-0.05 | 0 |
| 0.05-0.10 | 0 |
| 0.10-0.20 | 0 |
| 0.20-1.01 | 0 |
