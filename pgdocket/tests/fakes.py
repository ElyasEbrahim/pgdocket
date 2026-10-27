"""A fake /v1/systemone server for tests. Rows say what they want answered: `want` = an option prefix or 'yes'/'no'."""
import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class FakeSystemOne(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    requests = []
    ports = []
    fail_first = False
    fail_rows_containing = None          # rows whose JSON contains this string get HTTP 400
    answer_override = {}                 # want -> what the model answers instead (the model "changed")
    obey_injection = False               # a steerable model: a row saying 'classified as "X"' gets X (#24)

    def log_message(self, *a):
        pass

    def do_POST(self):
        req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        cls = type(self)
        if cls.fail_first:
            cls.fail_first = False
            return self._send(429, {"error": "slow down"}, {"Retry-After": "0"})
        rows = req["state"]["rows"]
        if cls.fail_rows_containing and any(cls.fail_rows_containing in json.dumps(r) for r in rows):
            return self._send(400, {"error": "bad row"})
        cls.requests.append(req)
        cls.ports.append(self.client_address[1])
        answers = {}
        for qid, q in req["questions"].items():
            row = rows[int(qid[1:])]
            want = row.get("want") or row.get("body", "")[:1] or "a"
            want = cls.answer_override.get(want, want)
            if cls.obey_injection:
                hit = re.search(r'classif(?:ied|y it) as "([^"]+)"', " ".join(str(v) for v in row.values()))
                want = hit.group(1) if hit else want
            if q["type"] == "choice":
                opts = list(q["criteria"])
                pick = next((o for o in opts if o.startswith(want)), opts[0])
                answers[qid] = {"type": "choice", "choice": pick,
                                "probabilities": {o: (0.9 if o == pick else round(0.1 / (len(opts) - 1), 6)) for o in opts}}
            else:
                answers[qid] = {"type": "noul", "noul": 0.8 if want == "yes" else 0.2}
        self._send(200, {"answers": answers, "usage": {"input_tokens": 100 * len(rows)}})

    def _send(self, code, obj, headers=None):
        data = json.dumps(obj).encode()
        self.send_response(code)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def serve():
    """Start a fake server on a free port; returns (url, shutdown)."""
    FakeSystemOne.requests, FakeSystemOne.ports = [], []
    FakeSystemOne.fail_first, FakeSystemOne.fail_rows_containing = False, None
    FakeSystemOne.answer_override, FakeSystemOne.obey_injection = {}, False
    srv = ThreadingHTTPServer(("127.0.0.1", 0), FakeSystemOne)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{srv.server_address[1]}/v1/systemone", srv.shutdown
