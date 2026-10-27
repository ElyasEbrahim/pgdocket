# pgdocket in 5 minutes

You need Docker and a TypeSafe API key ([console.typesafe.ai](https://console.typesafe.ai)). The whole quickstart costs well under **$0.01** in API calls (about $0.004 when last run, on 2026-10-22).

The database holds 60 **synthetic** support tickets (written for this example; see `seed.sql`). Each has a `team` column: the team a person routed it to. pgdocket will decide the team for every ticket, keep that decision current, and learn from the `team` labels how far to trust itself.

```bash
cd examples/quickstart
export TYPESAFE_API_KEY=...          # or put it in a .env file next to docker-compose.yml
docker compose up -d --wait db
```

**1. Install the schema.** This needs no superuser and no extension:

```bash
docker compose run --rm pgdocket install
# applied: 001, 002, 003, 004, 005, 006, 007, 008, 009
```

**2. Declare the decision.** Existing rows are queued; new and edited rows will be too:

```bash
docker compose run --rm pgdocket create triage --table shop.tickets --key id --text subject,body \
  --kind choice --question "Which team should handle this support ticket?" \
  --options billing,technical,account,sales
# decision triage (id 1) created; 60 rows queued; read it from docket.v_triage
```

**3. Start the worker.** It judges the queue, then waits for new rows. The 60 tickets take about 10 seconds; until then they show as `pending`:

```bash
docker compose up -d pgdocket
docker compose run --rm pgdocket status            # wait until it says "queued 0"
docker compose exec db psql -U postgres -d shop -c \
  "SELECT key, status, answer, round(confidence::numeric, 2) AS confidence FROM docket.v_triage ORDER BY key LIMIT 5"
#  key | status |  answer   | confidence
#    1 | judged | billing   |       1.00
#    2 | judged | technical |       1.00
#    3 | judged | account   |       0.78
```

**4. Add a ticket and watch it get decided.** The worker answers within a second or two; query again if it still says `pending`:

```bash
docker compose exec db psql -U postgres -d shop -c \
  "INSERT INTO shop.tickets (id, subject, body) VALUES (61, 'Charged for a seat we removed', 'We removed a user last month but the invoice still includes their seat.')"
docker compose exec db psql -U postgres -d shop -c "SELECT key, status, answer FROM docket.v_triage WHERE key = 61"
#  61 | judged | billing
```

**5. Calibrate on your labels.** Raw confidence is not a probability yet. Calibrate once the worker has answered the tickets, so the labels have answers to compare with:

```bash
docker compose run --rm pgdocket label triage --from-column team
docker compose run --rm pgdocket calibrate triage --precision-target 0.85
# triage: 60 labels · calibration error 0.079 → 0.063 · auto when the model's score ≥ 0.568: 95.0 % of labelled rows, precision ≥ 85 % (95 % confidence)
```

**6. Act on the answers you can trust:**

```bash
docker compose exec db psql -U postgres -d shop -c \
  "SELECT key, answer, round(raw_score::numeric, 2) AS score, round(confidence::numeric, 2) AS confidence, auto
   FROM docket.v_triage ORDER BY raw_score LIMIT 4"
#  33 | technical | 0.48 | 0.50 | f      ← below the threshold: send to a person
#  59 | technical | 0.52 | 0.53 | f
#   8 | sales     | 0.53 | 0.56 | f
#  24 | billing   | 0.57 | 0.63 | t
```

`auto = true` means the model's own score clears the threshold that keeps precision ≥ 85 % at 95 % confidence: 58 of 61 tickets in the run shown. Your threshold and count will differ a little, because the model's scores vary from run to run and the threshold is fitted on them; in another run all 61 cleared it. `confidence` is the calibrated probability, for reading.

**Why 85 % and not 90 %?** With only 60 labels, even 58 of 60 correct can't *prove* 90 % at 95 % confidence, so `calibrate --precision-target 0.9` honestly reports that no threshold qualifies. More labels make stricter targets provable. 100–200 is a good start.

**7. Try a change before switching it on.** Would one-line descriptions of the teams ([teams.json](teams.json)) help? Add them as a shadow version: the view keeps showing the current answers. Then compare both on every ticket, while the running worker does the judging:

```bash
docker compose run --rm -v "$PWD/teams.json:/teams.json:ro" pgdocket revise triage --options-file /teams.json
# triage v2 added as a shadow version; docket.v_triage still shows the active one
docker compose run --rm pgdocket compare triage 2 --sample 61 --wait 120
# ## Verdict: **no clear difference**
# - The new version changes 6.6 % of answers, against 1.6 % when the current one is asked again: …within run-to-run noise.
# - The versions disagree on 4 of 61 rows, all labelled: the current one was right on 1, the new one on 3 … (sign test p = 0.62).
# - Auto-accept at ≥ 85 % precision …: current 95.1 % of compared rows, new 100.0 %.
# - Tokens per row: 354 → 402 (+14 %).
```

`compare` re-asks the current version on the same tickets, so a change is only credited with what goes beyond the model's own run-to-run noise. Only the rows where the versions disagree decide which is more accurate. Here that's 4 tickets: a hint that the descriptions help, but too few to be sure. To switch anyway:

```bash
docker compose run --rm pgdocket promote triage 2
# triage: v2 is active; v1 is retired (last comparison: no clear difference)
# recalibrated v2 on 60 labels: calibration error 0.057 → 0.048 · auto when the model's score ≥ 0.510: 100.0 % of labelled rows
```

`promote` refits the calibration for the new version on the same labels, so `auto` keeps working. To drop the idea instead: `pgdocket retire triage 2`.

**Check the decision against your labels** in a full report (agreement, calibration, thresholds, cost):

```bash
docker compose run --rm --user root -v "$PWD/reports:/reports" pgdocket check \
  --table shop.tickets --key id --text subject,body \
  --kind choice --question "Which team should handle this support ticket?" \
  --options billing,technical,account,sales --label-column team --sample 60 --yes --out /reports/triage
# report: /reports/triage/report.md   → open reports/triage/report.md
```

**Clean up:** `docker compose down -v`

Your numbers may differ slightly: the model doesn't return identical probabilities on every run (see the [findings](../../docs/phase0/FINDINGS-jev-1.13.md)). Next: [pgdocket/README.md](../../pgdocket/README.md) for all options, [docs/phase1/ledger.md](../../docs/phase1/ledger.md) for how it works, and [docs/phase1/versions.md](../../docs/phase1/versions.md) for comparing versions.
