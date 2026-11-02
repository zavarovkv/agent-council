"""Shared business-state semantics, independent of executor lifecycle states."""

TERMINAL = {"completed", "canceled", "duplicate"}


def is_review(state):
    return state.get("name", "").strip().casefold() in {
        "in review",
        "review",
        "на проверке",
        "на ревью",
        "на согласовании",
    }


def is_blocked(state):
    return state.get("name", "").strip().casefold() in {
        "blocked",
        "заблокировано",
        "заблокирована",
        "блокировка",
    }


def first_state(workflow, category):
    states = [
        s
        for s in workflow["states"]["nodes"]
        if s["type"] == category and (category != "started" or not (is_review(s) or is_blocked(s)))
    ]
    if not states:
        raise ValueError("The team has no workflow state of the requested type.")
    return min(states, key=lambda s: (s["position"], s["id"]))


def named_state(workflow, predicate, name):
    states = [s for s in workflow["states"]["nodes"] if s["type"] == "started" and predicate(s)]
    if len(states) != 1:
        raise ValueError(f"Configure one unambiguous {name} state for this team.")
    return states[0]


def review_state(workflow):
    return named_state(workflow, is_review, "In Review")
