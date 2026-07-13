import pytest

from backend.approval import (
    APPROVED,
    DRAFTED,
    EDITED,
    REJECTED,
    apply_action,
)


def test_drafted_transitions():
    assert apply_action(DRAFTED, "approve") == APPROVED
    assert apply_action(DRAFTED, "reject") == REJECTED
    assert apply_action(DRAFTED, "edit") == EDITED


def test_edited_loops_back_and_can_be_decided():
    assert apply_action(EDITED, "edit") == EDITED
    assert apply_action(EDITED, "approve") == APPROVED
    assert apply_action(EDITED, "reject") == REJECTED


def test_decisions_can_be_changed():
    assert apply_action(APPROVED, "reject") == REJECTED  # un-approve
    assert apply_action(REJECTED, "approve") == APPROVED  # reconsider


def test_edit_is_always_allowed():
    for state in (DRAFTED, APPROVED, REJECTED, EDITED):
        assert apply_action(state, "edit") == EDITED


def test_illegal_transitions_are_rejected():
    with pytest.raises(ValueError):
        apply_action(APPROVED, "approve")  # already approved -> no-op is illegal
    with pytest.raises(ValueError):
        apply_action(REJECTED, "reject")  # already rejected
    with pytest.raises(ValueError):
        apply_action(DRAFTED, "publish")  # unknown action
    with pytest.raises(ValueError):
        apply_action("bogus", "approve")  # unknown state
