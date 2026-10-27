"""A decision spec (what is asked, of which backend) and how to read an answer under it."""
import numpy as np


def option_names(spec):
    return [o["name"] for o in spec.get("options", [])]


def option_display(spec):
    """What the model sees: 'name: description' when a description is given."""
    return [f"{o['name']}: {o['description']}" if o.get("description") else o["name"] for o in spec.get("options", [])]


def choice_vector(answer, display):
    """Probabilities aligned to `display`; accepts option-text or index keys (with a legend)."""
    probs = answer.get("probabilities") or {}
    legend = answer.get("legend") or {}
    v = np.zeros(len(display))
    for k, p in probs.items():
        name = legend.get(k, k)
        if name in display:
            v[display.index(name)] = float(p)
        elif str(k).isdigit() and int(k) < len(display):
            v[int(k)] = float(p)
    if v.sum() <= 0 and answer.get("choice") in display:
        v[display.index(answer["choice"])] = 1.0
    return v / v.sum() if v.sum() > 0 else np.full(len(display), 1 / len(display))


def read_answer(spec, answer):
    """(value, score): the option name and its probability (choice), or (None, yes-probability) (noul)."""
    if spec["kind"] == "noul":
        return None, float(answer["noul"])
    v = choice_vector(answer, option_display(spec))
    i = int(v.argmax())
    return option_names(spec)[i], float(v[i])


def validate(spec):
    if spec.get("kind") not in ("choice", "noul"):
        raise ValueError("spec.kind must be 'choice' or 'noul'")
    if not spec.get("question"):
        raise ValueError("spec.question is required")
    if spec["kind"] == "choice" and len(spec.get("options", [])) < 2:
        raise ValueError("a choice needs at least two options")
    if not isinstance(spec.get("untrusted", False), bool):
        raise ValueError("spec.untrusted must be true or false")
    return spec
