"""Application states and the only transitions allowed between them."""
DISCOVERED, RANKED, SELECTED, QUEUED, PREFLIGHT, PREPARING, FILLING = "Discovered", "Ranked", "Selected", "Queued", "Preflight", "Preparing", "Filling"
AWAITING, READY, SUBMITTING, SUBMITTED, UNCONFIRMED = "Awaiting User", "Ready to Submit", "Submitting", "Submitted", "Submission Unconfirmed"
FAILED, BLOCKED, DUPLICATE = "Failed", "Blocked", "Duplicate"

ALL = [DISCOVERED, RANKED, SELECTED, QUEUED, PREFLIGHT, PREPARING, FILLING, AWAITING, READY, SUBMITTING, SUBMITTED, UNCONFIRMED, FAILED, BLOCKED, DUPLICATE]
FINAL = {SUBMITTED, DUPLICATE}
ACTIVE = {QUEUED, PREFLIGHT, PREPARING, FILLING, READY, SUBMITTING}

ALLOWED = {
    DISCOVERED: {RANKED, SELECTED, BLOCKED},
    RANKED: {SELECTED, BLOCKED},
    SELECTED: {QUEUED, RANKED, BLOCKED},
    QUEUED: {PREFLIGHT, SELECTED, BLOCKED},
    PREFLIGHT: {PREPARING, AWAITING, BLOCKED, DUPLICATE, FAILED, SELECTED},
    PREPARING: {FILLING, AWAITING, FAILED, BLOCKED},
    FILLING: {READY, AWAITING, FAILED, BLOCKED},
    AWAITING: {QUEUED, SELECTED, BLOCKED, SUBMITTED, FILLING, READY},   # SUBMITTED: you confirm an assisted submission
    READY: {SUBMITTING, AWAITING, SELECTED, FAILED},
    SUBMITTING: {SUBMITTED, UNCONFIRMED, FAILED},
    UNCONFIRMED: {SUBMITTED, FAILED, SELECTED},       # you verify on the site, or retry deliberately
    FAILED: {QUEUED, SELECTED, BLOCKED},
    BLOCKED: {SELECTED},
    SUBMITTED: set(),
    DUPLICATE: set(),
}


class TransitionError(RuntimeError):
    pass


def check(old: str, new: str) -> None:
    if new not in ALLOWED.get(old, set()):
        raise TransitionError(f"An application cannot go from '{old}' to '{new}'")


def move(app, new: str, **fields) -> None:
    """Change an Application row's state, refusing illegal moves; `fields` are set alongside (error, blocker, …)."""
    check(app.state, new)
    app.state = new
    for k, v in fields.items(): setattr(app, k, v)
