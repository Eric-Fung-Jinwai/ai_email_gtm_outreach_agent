"""Tests for the shared edit re-evaluation + failure-reason helpers."""

from backend.email_ops import failure_reasons, reevaluate_edited_email

# A body that clears the deterministic gate: 40-200 words, has a CTA/question,
# no spam signals.
GOOD_BODY = (
    "Hi Ada, I noticed Acme has been hiring backend engineers, which usually signals "
    "real scaling pressure on the platform team. We help companies in exactly that spot "
    "cut infrastructure toil and ship features faster without adding headcount, and a few "
    "of your peers have seen meaningful wins in their first quarter. Would you be open to "
    "a short intro call next week so I can share what that looked like for them?"
)


def _judged_email(faithful=False):
    return {
        "id": "email-0",
        "subject": "Old subject",
        "body": "old body",
        "eval": {
            "passed": True,
            "ready": faithful,
            "checks": [],
            "judge": {"faithful": faithful, "claims": [{"claim": "raised $10M", "grounded": False}]},
        },
    }


def test_edit_nulls_judge_and_keeps_prior_verdict():
    email = _judged_email(faithful=False)
    ev = reevaluate_edited_email(email, "New subject", GOOD_BODY, calendar_link=None)
    assert ev["judge"] is None                       # stale verdict cleared
    assert ev["judge_stale"] is True                 # marked as edited-since-judged
    assert ev["prior_judge"]["faithful"] is False    # old verdict retained for the UI
    assert ev["ready"] is False                      # previously-judged edit is NOT auto-ready
    assert ev["passed"] is True                       # deterministic gate still ran and passed


def test_edit_without_prior_judge_is_ready_when_gate_passes():
    email = {"id": "e", "subject": "s", "body": "b", "eval": {"passed": True, "ready": True}}
    ev = reevaluate_edited_email(email, "New subject", GOOD_BODY, calendar_link=None)
    assert ev["judge_stale"] is False
    assert ev["ready"] is True  # no judge ever ran → normal deterministic readiness


def test_edit_honors_calendar_link_validation():
    email = {"id": "e", "subject": "s", "body": "b", "eval": {"passed": True}}
    # Calendar link required but absent from the edited body → not ready.
    ev = reevaluate_edited_email(email, "New subject", GOOD_BODY, calendar_link="https://cal.com/me")
    assert ev["passed"] is False
    assert any(c["name"] == "calendar_link_included" and not c["passed"] for c in ev["checks"])
    # Include the link → passes.
    ev2 = reevaluate_edited_email(
        email, "New subject", GOOD_BODY + " https://cal.com/me", calendar_link="https://cal.com/me"
    )
    assert ev2["passed"] is True


def test_failure_reasons_surfaces_binding_and_checks():
    email = {
        "eval": {
            "binding_error": "recipient is not in the supplied verified-contact set",
            "checks": [{"name": "word_count", "passed": False, "detail": "12 words"}],
            "judge": {"faithful": False, "claims": [{"claim": "raised $10M", "grounded": False}], "issues": ["too generic"]},
        }
    }
    reasons = failure_reasons(email)
    assert any("verified-contact set" in r for r in reasons)
    assert any("word_count" in r for r in reasons)
    assert any("raised $10M" in r for r in reasons)
    assert any("too generic" in r for r in reasons)
