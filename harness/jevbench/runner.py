"""Load the sample into Postgres and run the experiments through pg-jev's jev_eval()."""
import json
import os
import time
from datetime import datetime, timezone
from urllib.parse import urlsplit

from psycopg.types.json import Jsonb

from .db import connect, ensure_schema
from .tasks import default_experiments

BACKENDS = {
    # name: (api_url, default model)
    "mock": ("http://mock:8765/local/v1/systemone", "jev-mock"),
    "sim": ("http://sim:8787/v1/systemone", "jev-sim"),
    "jev": ("https://api.typesafe.ai/v1/systemone", "jev-1.13.0"),
}

# Jev list price (pg-jev uses the same constant); output tokens are free.
USD_PER_INPUT_TOKEN = 0.042 / 1_000_000


def load_sample(path, n=None):
    rows = [json.loads(line) for line in open(path)]
    if n:
        rows = rows[:n]
    with connect() as conn:
        ensure_schema(conn)
        with conn.cursor() as cur:
            cur.execute("TRUNCATE bench.complaints, bench.items")
            cur.execute("DROP TABLE IF EXISTS bench.items_original")   # order snapshot of the previous load
            with cur.copy("COPY bench.complaints (complaint_id, date_received, product_raw, product, issue, "
                          "sub_issue, narrative, text_hash) FROM STDIN") as cp:
                for r in rows:
                    cp.write_row((r["complaint_id"], r["date_received"], r["product_raw"], r["product"],
                                  r["issue"], r["sub_issue"], r["narrative"], r["text_hash"]))
            cur.execute("INSERT INTO bench.items (narrative) SELECT narrative FROM bench.complaints ORDER BY random()")
            cur.execute("ANALYZE bench.items")
            cur.execute("SELECT count(*) FROM bench.complaints WHERE text_hash <> md5(narrative)")
            bad = cur.fetchone()[0]
            if bad:
                raise RuntimeError(f"{bad} rows have a text_hash that does not match md5(narrative)")
            cur.execute("SELECT product, count(*) FROM bench.complaints GROUP BY 1 ORDER BY 2 DESC")
            dist = cur.fetchall()
        conn.commit()
    return len(rows), dist


def _batches(narrative_hashes, size=20):
    """pg-jev streams a table in physical order and sends consecutive chunks of `size` rows."""
    return {h: i // size for i, h in enumerate(narrative_hashes)}


def reorder(seed=None, restore=False, batch_size=20):
    """Re-insert bench.items in a new physical order, so pg-jev forms different batches; --restore undoes it.

    The first call snapshots the current order into bench.items_original (pos, narrative). A seeded order is
    reproducible: rows are sorted by md5(narrative || ':' || seed).
    """
    with connect() as conn, conn.cursor() as cur:
        cur.execute("CREATE TABLE IF NOT EXISTS bench.items_original AS "
                    "SELECT row_number() OVER (ORDER BY ctid) AS pos, narrative FROM bench.items")
        cur.execute("SELECT md5(narrative) FROM bench.items_original ORDER BY pos")
        original = [r[0] for r in cur.fetchall()]
        cur.execute("TRUNCATE bench.items")
        if restore:
            cur.execute("INSERT INTO bench.items (narrative) SELECT narrative FROM bench.items_original ORDER BY pos")
        else:
            cur.execute("INSERT INTO bench.items (narrative) SELECT narrative FROM bench.items_original "
                        "ORDER BY md5(narrative || ':' || %s)", (str(seed),))
        cur.execute("ANALYZE bench.items")
        cur.execute("SELECT md5(narrative) FROM bench.items ORDER BY ctid")
        now = [r[0] for r in cur.fetchall()]
        conn.commit()
    old_b, new_b = _batches(original, batch_size), _batches(now, batch_size)
    members_old, members_new = {}, {}
    for h, b in old_b.items():
        members_old.setdefault(b, set()).add(h)
    for h, b in new_b.items():
        members_new.setdefault(b, set()).add(h)
    shared = [len((members_old[old_b[h]] & members_new[new_b[h]]) - {h}) for h in now]
    return {"rows": len(now), "identical_order": now == original,
            "rows_with_no_shared_neighbour": sum(s == 0 for s in shared) / max(len(shared), 1),
            "mean_shared_neighbours": sum(shared) / max(len(shared), 1)}


REQUEST_OVERHEAD_TOKENS = 270   # pg-jev's measured per-request overhead


def estimate_cost(conn, experiments, batch_size=None):
    """Upper-bound input tokens: ~chars/3.5 per row plus per-question overhead (options, instructions), plus the
    extra request overhead when batches are smaller than pg-jev's default of 20."""
    with conn.cursor() as cur:
        cur.execute("SELECT count(*), coalesce(sum(length(narrative)), 0) FROM bench.items")
        n, chars = cur.fetchone()
    tokens = 0
    for e in experiments:
        per_row_overhead = 60 + sum(len(o) for o in e.options) // 3 + len(e.question) // 3
        tokens += chars / 3.5 + n * per_row_overhead
        if batch_size and batch_size < 20:
            tokens += n * REQUEST_OVERHEAD_TOKENS * (1 / batch_size - 1 / 20)
    return n, int(tokens), tokens * USD_PER_INPUT_TOKEN


def _configure(cur, api_url, model, n_items, api_key, batch_size=None):
    if batch_size:
        cur.execute("SELECT set_config('jev.batch_size', %s, false)", (str(batch_size),))
    # Room to send rows twice: pg-jev sends a batch again when its request fails after retries (a busy local
    # server), and counts those rows again; with the cap at exactly n_items one failure ended the run (#4).
    cur.execute("SELECT set_config('jev.api_url', %s, false), set_config('jev.model', %s, false), "
                "set_config('jev.notices', 'off', false), "
                "set_config('jev.max_rows_per_statement', %s, false)",
                (api_url, model, str(2 * n_items)))
    if api_key:
        cur.execute("SELECT set_config('jev.api_key', %s, false)", (api_key,))


def run(backend, model=None, api_url=None, experiments=None, max_usd=1.0, notes=None, yes=False, batch_size=None):
    if backend not in BACKENDS and not api_url:
        raise SystemExit(f"unknown backend {backend!r}; use one of {sorted(BACKENDS)} or pass --api-url")
    default_url, default_model = BACKENDS.get(backend, (api_url, "jev-latest"))
    api_url, model = api_url or default_url, model or default_model
    experiments = experiments or default_experiments()
    host = (urlsplit(api_url).hostname or "").lower()
    paid = host == "typesafe.ai" or host.endswith(".typesafe.ai")
    api_key = (os.environ.get("TYPESAFE_API_KEY") or None) if paid else None     # TypeSafe's key goes to TypeSafe only

    with connect() as conn:
        ensure_schema(conn)
        n_items, tokens, usd = estimate_cost(conn, experiments, batch_size)
        if n_items == 0:
            raise SystemExit("bench.items is empty: run `fetch` and `load` first")
        print(f"{len(experiments)} experiments × {n_items} rows; estimated ≤ {tokens:,} input tokens"
              + (f" ≈ ${usd:.3f} at Jev list price" if paid else " (free backend)"))
        if paid:
            if not api_key:
                raise SystemExit("TYPESAFE_API_KEY is not set in the harness environment")
            if usd > max_usd:
                raise SystemExit(f"estimated cost ${usd:.2f} exceeds --max-usd {max_usd}; lower N or raise the cap")
            if not yes:
                raise SystemExit("paid backend: re-run with --yes to confirm the estimated spend above")

        run_id = f"{backend}-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"
        with conn.cursor() as cur:
            cur.execute("INSERT INTO bench.runs (run_id, backend, api_url, model, n_items, settings, notes) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                        (run_id, backend, api_url, model, n_items,
                         Jsonb({"experiments": [e.name for e in experiments], "batch_size": batch_size or 20}),
                         notes))
        conn.commit()

    for e in experiments:
        # A fresh session per experiment: pg-jev's answer cache is per session and its key does not include
        # jev.model or jev.api_url, so reusing a session across backends could return another backend's answers.
        with connect() as conn, conn.cursor() as cur:
            _configure(cur, api_url, model, n_items, api_key, batch_size)
            cur.execute("SELECT jev_cache_clear()")
            cur.execute("SELECT jev_stats()")
            before = cur.fetchone()[0]
            t0 = time.time()
            cur.execute("SELECT md5(i.narrative), jev_eval(i, %s, %s, %s::text[]) FROM bench.items i",
                        (e.question, e.kind, e.options_list))
            rows = cur.fetchall()
            secs = time.time() - t0
            cur.execute("SELECT jev_stats()")
            after = cur.fetchone()[0]
            delta = {k: round(after[k] - before.get(k, 0), 6) for k in after
                     if isinstance(after[k], (int, float)) and k not in ("in_flight", "connections", "cached_answers")}
            cur.execute("INSERT INTO bench.experiment_runs (run_id, experiment, kind, question, options, family, "
                        "seconds, stats) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                        (run_id, e.name, e.kind, e.question, e.options_list, e.family, secs, Jsonb(delta)))
            with cur.copy("COPY bench.results (run_id, experiment, text_hash, answer) FROM STDIN") as cp:
                for h, ans in rows:
                    cp.write_row((run_id, e.name, h, json.dumps(ans)))
            conn.commit()
        print(f"  {e.name:<24} {len(rows):>6} rows  {secs:7.1f}s  requests={delta.get('requests', 0):.0f} "
              f"tokens={delta.get('input_tokens', 0):,.0f}" + (f"  cost=${delta.get('estimated_cost_usd', 0):.4f}" if paid else ""))

    with connect() as conn, conn.cursor() as cur:
        cur.execute("UPDATE bench.runs SET finished_at = now() WHERE run_id = %s", (run_id,))
        conn.commit()
    return run_id


def cache_check(question="the complaint is about a debt collector or about collecting a debt"):
    """Does pg-jev reuse answers across backends within one session? (cache key lacks model and api_url)."""
    with connect() as conn, conn.cursor() as cur:
        ensure_schema(conn)
        cur.execute("SELECT jev_cache_clear()")
        out = {}
        for backend in ("mock", "sim"):
            url, model = BACKENDS[backend]
            _configure(cur, url, model, 1_000_000, None)
            cur.execute("SELECT md5(i.narrative), jev_prob(i, %s) FROM bench.items i", (question,))
            out[backend] = dict(cur.fetchall())
        cur.execute("SELECT jev_cache_clear()")
        _configure(cur, *BACKENDS["sim"], 1_000_000, None)
        cur.execute("SELECT md5(i.narrative), jev_prob(i, %s) FROM bench.items i", (question,))
        fresh_sim = dict(cur.fetchall())
    same_as_mock = sum(out["sim"][h] == out["mock"][h] for h in out["mock"])
    same_as_fresh = sum(out["sim"][h] == fresh_sim[h] for h in fresh_sim)
    return {"rows": len(out["mock"]), "sim_after_mock_equals_mock": same_as_mock,
            "sim_after_mock_equals_fresh_sim": same_as_fresh,
            "leak": same_as_mock == len(out["mock"]) and same_as_fresh < len(fresh_sim)}
