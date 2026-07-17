"""Shared email operations that both frontends (Streamlit + FastAPI) reuse.

Extracted so the edit-and-re-evaluate logic lives in one tested place instead of
being duplicated in each UI. Pure and Streamlit-free.
"""

from typing import Any, Dict, List, Optional

from backend.evaluation.deterministic import evaluate_email
from backend.evaluation.gating import ready_after_edit


def reevaluate_edited_email(
    email: Dict[str, Any],
    new_subject: str,
    new_body: str,
    calendar_link: Optional[str],
) -> Dict[str, Any]:
    """Re-run the deterministic gate on a human-edited draft and return the new
    ``eval`` dict.

    Takes the *existing* email (with its current ``eval``) so it can preserve the
    audit trail: the prior judge verdict no longer applies to the new text, so we
    null ``judge``, mark ``judge_stale`` when there had been one, keep the old
    verdict under ``prior_judge`` (for the UI to explain why it was flagged), and
    compute readiness via :func:`ready_after_edit` — a previously-judged draft is
    NOT auto-ready after an edit. The deterministic gate runs *with* the calendar
    link so an edited email isn't silently exempted from calendar-link validation.
    """
    prior_judge = (email.get("eval") or {}).get("judge")
    had_judge = prior_judge is not None
    candidate = {**email, "subject": new_subject, "body": new_body}
    ev = evaluate_email(candidate, calendar_link=calendar_link or None).model_dump()
    ev["judge"] = None  # verdict no longer applies to the edited text
    ev["judge_stale"] = had_judge
    ev["prior_judge"] = prior_judge  # kept so we can show why it was flagged
    ev["ready"] = ready_after_edit(had_judge, ev)
    return ev


def failure_reasons(email: Dict[str, Any]) -> List[str]:
    """Human-readable reasons a draft is not ready, for an informed override.
    Shared by both frontends so the explanation stays consistent."""
    ev = email.get("eval") or {}
    reasons: List[str] = []
    if ev.get("binding_error"):
        reasons.append(ev["binding_error"])
    for c in ev.get("checks", []):
        if not c.get("passed"):
            reasons.append(f"Check `{c['name']}`: {c.get('detail') or 'failed'}")
    judge = ev.get("judge")
    if judge and judge.get("error"):
        reasons.append(f"Faithfulness judge unavailable: {judge['error']}")
    if judge and judge.get("faithful") is False:
        for c in judge.get("claims", []):
            if not c.get("grounded"):
                reasons.append(f"LLM judge — unsupported claim: {c.get('claim', '')}")
        for issue in judge.get("issues", []):
            reasons.append(f"LLM judge: {issue}")
    prior = ev.get("prior_judge")
    if ev.get("judge_stale") and prior and prior.get("faithful") is False:
        claims = "; ".join(c.get("claim", "") for c in prior.get("claims", []) if not c.get("grounded"))
        reasons.append(
            "Before your edit the LLM judge flagged: "
            + (claims or "unfaithful")
            + " (edited text was not re-checked)"
        )
    return reasons
