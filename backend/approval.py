"""Email approval state machine (Phase 5, human-in-the-loop).

A draft moves ``drafted -> {approved | rejected | edited}``. ``edit`` is always
allowed and loops back to ``edited`` (a human-edited draft must be re-reviewed),
so ``edited`` is a state, not a terminal action. Approve/reject can also change a
prior decision, but you cannot repeat the same terminal action (that would be a
no-op and signals a UI bug) — those are rejected as illegal transitions.
"""

from typing import Dict

DRAFTED = "drafted"
APPROVED = "approved"
REJECTED = "rejected"
EDITED = "edited"

APPROVE = "approve"
REJECT = "reject"
EDIT = "edit"

ACTIONS = {APPROVE, REJECT, EDIT}

# state -> {action -> next_state}
_TRANSITIONS: Dict[str, Dict[str, str]] = {
    DRAFTED: {APPROVE: APPROVED, REJECT: REJECTED, EDIT: EDITED},
    EDITED: {APPROVE: APPROVED, REJECT: REJECTED, EDIT: EDITED},
    APPROVED: {REJECT: REJECTED, EDIT: EDITED},  # can un-approve, can't re-approve
    REJECTED: {APPROVE: APPROVED, EDIT: EDITED},  # can reconsider, can't re-reject
}


def apply_action(state: str, action: str) -> str:
    """Return the next status, or raise ``ValueError`` on an illegal transition."""
    if action not in ACTIONS:
        raise ValueError(f"unknown action: {action!r}")
    allowed = _TRANSITIONS.get(state)
    if allowed is None:
        raise ValueError(f"unknown state: {state!r}")
    if action not in allowed:
        raise ValueError(f"cannot '{action}' from '{state}'")
    return allowed[action]
