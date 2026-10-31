# Draft issue for NandhaKishorM/laya: questions about `rows[i]` in a shared state all get one answer

*Draft, not posted. Found while comparing Laya with Jev (#4, [backends.md](backends.md)).*

---

**Title:** laya-serve: in a multi-row state, every question gets the answer for the whole state

**Version:** laya 0.4.16 (`laya[serve]`), CPU, `"model": "laya"` (the English checkpoint).

Jev clients such as [pg-jev](https://github.com/realZachi/pg-jev) send several records in one request: one `state` with `rows`, and one question per record whose instructions point at it (`For the record rows[3]: …`). Jev answers each question about its own row. laya-serve answers every question in the request from the whole state, so the rows of a request get the same answer, or nearly.

**Reproduce.** Two Kubernetes-style issues, one about storage (A) and one about networking (B), with the question "Which Kubernetes SIG should handle this issue?" and ten options (SIGs, each with a one-line description):

| request | answer for `rows[0]` | answer for `rows[1]` |
| --- | --- | --- |
| A alone | storage (0.946) | – |
| B alone | network (0.647) | – |
| A and B together | storage (0.493) | storage (0.495) |
| B and A together | storage (0.669) | storage (0.673) |

Each question's `instructions` is `For the record \`rows[i]\`: Which Kubernetes SIG (special interest group) should handle this issue?`, with the same `criteria` for both.

In one request of 20 Kubernetes issues, 16 came back *testing* and 4 *api-machinery*; judged one per request, the same issues got five different answers.

**Impact.** On 100 labelled Kubernetes issues (10 options), sending 20 rows per request instead of one changed 76 % of the answers, and accuracy fell from 38 % to 12 %. pg-jev sends 20 rows per request by default, so pg-jev with laya-serve gives answers near chance, with no error or warning. On a yes/no question, 53 % of answers changed.

**Possible fixes.**
- Judge each question against its own row when the instructions reference `rows[i]`: split a multi-row state into one state per row, as `/v1/systemone/batch` already does for a `states` array.
- Or refuse such requests with a 422, or document the limitation in the Jev-compatibility notes, so clients send one row per request or use `/v1/systemone/batch`.
