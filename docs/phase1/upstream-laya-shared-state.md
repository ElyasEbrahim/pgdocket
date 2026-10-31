# Reported to Laya: with several rows in one state, every question gets the answer for the whole state

*Posted on 2026-10-31 as [NandhaKishorM/laya#1425](https://github.com/NandhaKishorM/laya/issues/1425). Found while comparing Laya with Jev (#4, [backends.md](backends.md)). The text below is what was posted.*

---

### What happened

Jev clients such as [pg-jev](https://github.com/realZachi/pg-jev) send several records in one request: one `state` with `rows`, and one question per record whose instructions point at it (``For the record `rows[3]`: …``). TypeSafe's Jev answers each question about its own row. Laya answers every question from the whole state, so the rows of a request get the same answer, or nearly. I expected each question to be answered about the row it names.

### Steps to reproduce

```python
import laya

agent = laya.load("convaiinnovations/laya")
A = {"title": "PersistentVolume stuck in Terminating after CSI driver upgrade",
     "body": "After upgrading the CSI driver, PVCs and PersistentVolumes stay in Terminating; the volume attachment never detaches."}
B = {"title": "kube-proxy drops Service traffic when an EndpointSlice changes",
     "body": "With IPVS mode, Service ClusterIP connections are reset whenever endpoints change; DNS lookups also time out."}
criteria = {"storage": "volumes, persistent storage, CSI drivers",
            "network": "Services, kube-proxy, DNS, network policy",
            "node": "kubelet, container runtime, node lifecycle"}


def question(i):
    return {"type": "choice", "criteria": criteria,
            "instructions": f"For the record `rows[{i}]`: Which Kubernetes SIG should handle this issue?"}


for name, rows in (("A alone", [A]), ("B alone", [B]), ("A, B", [A, B]), ("B, A", [B, A])):
    out = agent.system_one({"rows": rows}, {f"r{i}": question(i) for i in range(len(rows))})
    answers = [out["answers"][f"r{i}"] for i in range(len(rows))]
    print(f"{name:8}", [(a["choice"], round(a["probabilities"][a["choice"]], 3)) for a in answers])
```

Output (CPU):

```
A alone  [('storage', 0.956)]
B alone  [('network', 0.784)]
A, B     [('storage', 0.829), ('storage', 0.831)]
B, A     [('storage', 0.846), ('storage', 0.838)]
```

The networking issue B is answered *storage* whenever it shares a state with A, in either order. The same happens through `laya-serve` on `/v1/systemone` with `"model": "laya"`.

### Impact

On 100 labelled Kubernetes issues (ten SIG options with descriptions), sending 20 rows per request instead of one changed 76 % of the answers, and accuracy fell from 38 % to 12 %. In one request of 20 issues, 16 came back *testing*. pg-jev sends 20 rows per request by default, so pg-jev with laya-serve gives answers near chance, with no error or warning. On a yes/no question, 53 % of answers changed.

### Possible fixes

- Judge each question against its own row when the instructions reference `rows[i]`: split a multi-row state into one state per row, as `/v1/systemone/batch` already does for a `states` array.
- Or refuse such requests with a 422, or document the limitation in the Jev-compatibility notes, so that clients send one row per request or use `/v1/systemone/batch`.

### Environment

- Laya version: 0.4.16 (`laya[serve]`, checkpoint `convaiinnovations/laya`)
- Python version: 3.11.17
- Operating system: Linux (Docker on macOS, aarch64)
- Device or backend: CPU

Found while comparing Laya with TypeSafe's Jev on the same labelled rows; the full results are [here](https://github.com/ElyasEbrahim/pgdocket/blob/master/docs/phase1/backends.md).
