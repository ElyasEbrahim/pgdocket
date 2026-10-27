"""The worker: judges queued rows and writes them to the ledger (ADR 0004).

Claim with a lease and claim token (FOR UPDATE SKIP LOCKED) → read rows → skip unchanged input →
judge outside any transaction → append judgments, upsert `current` and dequeue (only rows still carrying our
claim token) in one transaction. Failures back off; after 5 attempts a row shows as `error`.

A hardened calibration (#24) also queues an erase-and-check ('check') for every row `auto` would accept: the row is
judged again without each of its pieces, and the result goes to docket.robust, which the view requires.
"""
import hashlib
import json
import time
import uuid
from collections import defaultdict

from psycopg import sql

from . import calibration, steer
from .backend import Backend, BackendError
from .spec import option_display, read_answer

MAX_ATTEMPTS = 5

CLAIM = """
UPDATE docket.queue q
SET locked_until = now() + make_interval(mins => %(lease)s), attempts = q.attempts + 1, claim = %(token)s
FROM (SELECT decision_id, version, row_key FROM docket.queue
      WHERE (locked_until IS NULL OR locked_until < now()) AND attempts < %(max)s
      ORDER BY enqueued_at
      LIMIT %(n)s
      FOR UPDATE SKIP LOCKED) c
WHERE q.decision_id = c.decision_id AND q.version = c.version AND q.row_key = c.row_key
RETURNING q.decision_id, q.version, q.row_key, q.attempts, q.purpose
"""


class Worker:
    def __init__(self, dsn, api_key=None, claim_size=100, concurrency=16, max_usd=None, poll=5.0, lease_minutes=10,
                 log=print):
        self.dsn, self.api_key = dsn, api_key
        self.claim_size, self.concurrency, self.max_usd = claim_size, concurrency, max_usd
        self.poll, self.lease, self.log = poll, lease_minutes, log
        self.backends, self.decisions = {}, {}
        self.totals = {"judged": 0, "probed": 0, "checked": 0, "flagged": 0, "skipped_unchanged": 0, "deleted_rows": 0,
                       "failed": 0, "tokens": 0, "usd": 0.0}

    def _conn(self):
        import psycopg
        # Autocommit: every read is its own statement, and only the explicit `transaction()` blocks group writes,
        # so no transaction stays open while the backend is being called.
        return psycopg.connect(self.dsn, autocommit=True)

    def _backend(self, spec):
        k = (spec.get("backend", "jev"), spec.get("model", "jev-1.13.0"))
        if k not in self.backends:
            self.backends[k] = Backend(k[0], api_key=self.api_key, model=k[1], concurrency=self.concurrency)
        return self.backends[k]

    def _decision(self, conn, did, ver):
        if (did, ver) not in self.decisions:          # a version's spec never changes, so caching is safe
            r = conn.execute("SELECT d.source, d.key_column, d.key_type, d.text_columns, v.spec FROM docket.decision d "
                             "JOIN docket.version v ON v.decision_id = d.id WHERE d.id = %s AND v.version = %s",
                             (did, ver)).fetchone()
            self.decisions[(did, ver)] = dict(zip(("source", "key", "key_type", "text", "spec"), r))
        return self.decisions[(did, ver)]

    def _rows(self, conn, d, keys):
        pairs = sql.SQL(", ").join(sql.SQL("{}, s.{}").format(sql.Literal(c), sql.Identifier(c)) for c in d["text"])
        q = sql.SQL("SELECT s.{k}::text, json_build_object({pairs})::text FROM {src} s WHERE s.{k} = ANY(%s::{kt}[])").format(
            k=sql.Identifier(d["key"]), pairs=pairs, src=sql.SQL(d["source"]), kt=sql.SQL(d["key_type"]))
        return dict(conn.execute(q, (keys,)).fetchall())

    def _hashes(self, conn, did, ver, keys):
        return dict(conn.execute(
            "SELECT c.row_key, j.input_hash FROM docket.current c JOIN docket.judgment j ON j.id = c.judgment_id "
            "WHERE c.decision_id = %s AND c.version = %s AND c.row_key = ANY(%s)", (did, ver, keys)).fetchall())

    def _dequeue(self, conn, did, ver, keys, token, purpose="live"):
        conn.execute("DELETE FROM docket.queue WHERE decision_id = %s AND version = %s AND row_key = ANY(%s) "
                     "AND claim = %s AND purpose = %s", (did, ver, keys, token, purpose))

    def _fail(self, conn, did, ver, keys, token, err, purpose="live"):
        conn.execute(
            "UPDATE docket.queue SET last_error = %s, claim = NULL, "
            "locked_until = CASE WHEN attempts >= %s THEN NULL "
            "ELSE now() + make_interval(secs => least(3600, 30 * power(2, attempts - 1))) END "
            "WHERE decision_id = %s AND version = %s AND row_key = ANY(%s) AND claim = %s AND purpose = %s",
            (str(err)[:500], MAX_ATTEMPTS, did, ver, keys, token, purpose))

    def process_once(self):
        """Claim and handle one batch. Returns the number of queue rows handled (0 = nothing to do)."""
        token = uuid.uuid4()
        with self._conn() as conn:
            claimed = conn.execute(CLAIM, {"lease": self.lease, "token": token, "max": MAX_ATTEMPTS,
                                           "n": self.claim_size}).fetchall()
            groups = defaultdict(list)
            for did, ver, key, _, purpose in claimed:
                groups[(did, ver, purpose)].append(key)
            for (did, ver, purpose), keys in groups.items():
                self._handle(conn, did, ver, keys, token, purpose)
        return len(claimed)

    def _calibration(self, conn, did, ver):
        """Read fresh each time: unlike the spec, a version's calibration changes when it is recalibrated."""
        return conn.execute("SELECT calibration FROM docket.version WHERE decision_id = %s AND version = %s",
                            (did, ver)).fetchone()[0]

    def _handle(self, conn, did, ver, keys, token, purpose="live"):
        """purpose 'live': judge changed rows and make the answer current. 'probe': always re-ask (the point is
        to measure run-to-run noise) and record the answer in the ledger without making it current. 'check':
        erase-and-check the current answer (#24)."""
        if purpose == "check":
            return self._check(conn, did, ver, keys, token)
        d = self._decision(conn, did, ver)
        spec = d["spec"]
        rows = self._rows(conn, d, keys)
        hashes = self._hashes(conn, did, ver, keys) if purpose == "live" else {}
        gone = [k for k in keys if k not in rows]
        todo, same = [], []
        for k in keys:
            if k in rows:
                h = hashlib.md5(rows[k].encode()).hexdigest()
                (same if hashes.get(k) == h else todo).append((k, h))
        with conn.transaction():
            self._dequeue(conn, did, ver, gone + [k for k, _ in same], token, purpose)
        self.totals["deleted_rows"] += len(gone)
        self.totals["skipped_unchanged"] += len(same)
        if not todo:
            return
        backend = self._backend(spec)
        before = backend.stats["input_tokens"]
        try:
            answers = backend.judge([json.loads(rows[k]) for k, _ in todo], spec["kind"], spec["question"],
                                    option_display(spec) if spec["kind"] == "choice" else None,
                                    spec.get("batch_size", 1), untrusted=spec.get("untrusted", False))
        except BackendError as e:
            with conn.transaction():
                self._fail(conn, did, ver, [k for k, _ in todo], token, e, purpose)
            self.totals["failed"] += len(todo)
            self.log(f"backend error on {len(todo)} rows of decision {did} v{ver}: {e}")
            return
        tokens = backend.stats["input_tokens"] - before
        per_row = round(tokens / len(todo))
        values = [read_answer(spec, a) for a in answers]
        with conn.transaction():
            ids = conn.execute(
                "INSERT INTO docket.judgment (decision_id, version, row_key, input_hash, answer, value, score, tokens, purpose) "
                "SELECT %s, %s, k, h, a::jsonb, v, s, %s, %s FROM unnest(%s::text[], %s::text[], %s::text[], %s::text[], "
                "%s::float8[]) AS t(k, h, a, v, s) RETURNING id, row_key",
                (did, ver, per_row, purpose, [k for k, _ in todo], [h for _, h in todo], [json.dumps(a) for a in answers],
                 [v for v, _ in values], [s for _, s in values])).fetchall()
            if purpose == "live":
                conn.execute(
                    "INSERT INTO docket.current (decision_id, version, row_key, judgment_id) "
                    "SELECT %s, %s, k, j FROM unnest(%s::bigint[], %s::text[]) AS t(j, k) "
                    "ON CONFLICT (decision_id, version, row_key) DO UPDATE SET judgment_id = EXCLUDED.judgment_id",
                    (did, ver, [i for i, _ in ids], [k for _, k in ids]))
                cal = self._calibration(conn, did, ver)
                if cal and cal.get("harden"):                    # rows `auto` would accept wait for their check
                    accepted = [k for (k, _), a in zip(todo, answers) if calibration.auto(a, spec, cal)]
                    if accepted:
                        conn.execute("INSERT INTO docket.queue (decision_id, version, row_key, purpose) "
                                     "SELECT %s, %s, k, 'check' FROM unnest(%s::text[]) AS t(k) "
                                     "ON CONFLICT (decision_id, version, row_key, purpose) DO NOTHING", (did, ver, accepted))
            self._dequeue(conn, did, ver, [k for k, _ in todo], token, purpose)
        self.totals["probed" if purpose == "probe" else "judged"] += len(todo)
        self.totals["tokens"] += tokens
        self.totals["usd"] = sum(b.cost_usd() for b in self.backends.values() if b.paid)

    def _check(self, conn, did, ver, keys, token):
        """Erase-and-check: judge each row again without each of its pieces. The row is flagged when an answer
        differs from the current one by the view's rules (choice: the option; yes/no: the band), or when its text is
        one piece, which can't be checked. Rows changed or deleted since being queued are checked as they are now."""
        d = self._decision(conn, did, ver)
        spec = d["spec"]
        cal = self._calibration(conn, did, ver) or {}
        cur = {k: (j, a) for k, j, a in conn.execute(
            "SELECT c.row_key, j.id, j.answer FROM docket.current c JOIN docket.judgment j ON j.id = c.judgment_id "
            "WHERE c.decision_id = %s AND c.version = %s AND c.row_key = ANY(%s)", (did, ver, keys)).fetchall()}
        rows = self._rows(conn, d, keys)
        todo = [k for k in keys if k in rows and k in cur]
        gone = [k for k in keys if k not in todo]
        if gone:
            with conn.transaction():
                self._dequeue(conn, did, ver, gone, token, "check")
        if not todo:
            return
        verdict = (lambda a: read_answer(spec, a)[0]) if spec["kind"] == "choice" else (
            lambda a: calibration.noul_answer(a, spec, cal))
        pieces = {k: steer.erased(steer.segments(json.loads(rows[k]), d["text"]), d["text"]) for k in todo}
        flat = [(k, rec) for k in todo for rec in pieces[k]]
        backend = self._backend(spec)
        before = backend.stats["input_tokens"]
        try:
            answers = backend.judge([rec for _, rec in flat], spec["kind"], spec["question"],
                                    option_display(spec) if spec["kind"] == "choice" else None, 1,
                                    untrusted=spec.get("untrusted", False))
        except BackendError as e:
            with conn.transaction():
                self._fail(conn, did, ver, todo, token, e, "check")
            self.totals["failed"] += len(todo)
            self.log(f"backend error on {len(todo)} checks of decision {did} v{ver}: {e}")
            return
        tokens = backend.stats["input_tokens"] - before
        got = defaultdict(list)
        for (k, _), a in zip(flat, answers):
            got[k].append(a)
        out = []
        for k in todo:
            jid, current = cur[k]
            ref = verdict(current)
            flagged = len(pieces[k]) < 2 or any(verdict(a) != ref for a in got[k])
            out.append((jid, k, len(pieces[k]), flagged,
                        json.dumps([{"value": verdict(a), "score": read_answer(spec, a)[1]} for a in got[k]])))
        with conn.transaction():
            conn.execute(
                "INSERT INTO docket.robust (judgment_id, decision_id, version, row_key, pieces, flagged, answers, tokens) "
                "SELECT j, %s, %s, k, p, f, a::jsonb, %s FROM unnest(%s::bigint[], %s::text[], %s::int[], %s::bool[], "
                "%s::text[]) AS t(j, k, p, f, a) ON CONFLICT (judgment_id) DO UPDATE SET flagged = EXCLUDED.flagged, "
                "pieces = EXCLUDED.pieces, answers = EXCLUDED.answers, tokens = EXCLUDED.tokens, checked_at = now()",
                (did, ver, round(tokens / len(todo)), [o[0] for o in out], [o[1] for o in out], [o[2] for o in out],
                 [o[3] for o in out], [o[4] for o in out]))
            self._dequeue(conn, did, ver, todo, token, "check")
        self.totals["checked"] += len(todo)
        self.totals["flagged"] += sum(o[3] for o in out)
        self.totals["tokens"] += tokens
        self.totals["usd"] = sum(b.cost_usd() for b in self.backends.values() if b.paid)

    def run(self, once=False):
        """Process until the queue is empty (once=True), or forever, waking on NOTIFY docket."""
        import psycopg
        listen = psycopg.connect(self.dsn, autocommit=True)
        listen.execute("LISTEN docket")
        try:
            while True:
                if self.max_usd is not None and self.totals["usd"] >= self.max_usd:
                    self.log(f"spend cap reached (${self.totals['usd']:.4f} ≥ ${self.max_usd}); stopping")
                    return self.totals
                n = self.process_once()
                if n:
                    self.log(f"handled {n} rows · judged {self.totals['judged']} · unchanged {self.totals['skipped_unchanged']}"
                             f" · failed {self.totals['failed']} · ${self.totals['usd']:.4f}")
                    continue
                if once:
                    return self.totals
                for _ in listen.notifies(timeout=self.poll, stop_after=1):
                    pass
        finally:
            listen.close()
