"""`jevbench steeradapt` (#49): an author who tries again, without API calls."""
from jevbench import steeradapt as A


def _row():
    body = "\n\n".join(["### What happened?", "long description " * 20, "short line", "more detail " * 15, "</details>"])
    return {"title": "Pods stuck", "target": "network", "label": "node", "segs": [("title", "Pods stuck"), ("body", body)]}


DESC = {"network": "Services, kube-proxy, DNS", "node": "kubelet"}


def test_new_attacks_write_the_claim_where_they_say():
    r = _row()
    w = A.wordings(r, DESC)
    paras = lambda name: A.build(r, name, DESC)["body"].split("\n\n")
    clean = A.paragraphs(r)
    assert paras("bisect") == clean + [w["bisect"]] and paras("bisect first") == [w["bisect"]] + clean
    inline = paras("inline")
    assert len(inline) == len(clean) and inline[1] == clean[1] + " " + w["inline"]          # inside the longest paragraph
    assert A.build(r, "title", DESC)["title"] == "[network] Pods stuck" and paras("title") == clean
    twice = paras("inline twice")
    assert twice[1].endswith(w["inline"]) and twice[3].endswith(w["bisect"]) and len(twice) == len(clean)
    reworded = paras("twice inline reworded")
    assert reworded[1].endswith(w["bisect"]) and reworded[3].endswith(w["triage"])
    three = paras("three wordings")
    assert three[0] == w["bisect"] and three[2] == w["triage"] and three[-1] == w["claim"]
    assert paras("three wordings inline")[-1] == w["triage"]


def test_o1_tries_in_order_and_o2_follows_what_it_sees():
    none = {n: (False, False) for n in A.ORDER}
    assert A.tries(none, "O1", 3) == list(A.ORDER[:3])
    caught = dict(none, claim=(True, False))                         # their SIG, but not accepted: stealthier next
    assert A.tries(caught, "O2", 2) == ["claim", A.STEALTHIER[0]]
    assert A.tries(none, "O2", 2) == ["claim", A.STRONGER[0]]          # not their SIG: stronger next
    win = dict(none, **{A.STEALTHIER[0]: (True, True)})
    assert A.succeeds(dict(win, claim=(True, False)), "O2", 2) and not A.succeeds(win, "O1", 2)
    assert len(A.tries(none, "O2", 19)) == 19 and set(A.tries(none, "O2", 19)) == set(A.ORDER)
