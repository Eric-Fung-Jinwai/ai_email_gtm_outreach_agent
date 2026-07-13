"""Overall email readiness — combines the deterministic gate with the judge."""

from typing import Any, Dict


def email_is_ready(eval_result: Dict[str, Any]) -> bool:
    """An email is ready to send only if it passed the deterministic gate AND
    (no LLM judge ran, or the judge found it faithful). Unfaithful drafts and
    judge errors (``faithful`` is not ``True``) are NOT ready → route to review.
    """
    if not eval_result.get("passed"):
        return False
    judge = eval_result.get("judge")
    if judge is None:
        return True
    return judge.get("faithful") is True


def ready_after_edit(prev_had_judge: bool, new_eval: Dict[str, Any]) -> bool:
    """Readiness for a human-edited draft.

    If the draft had been faithfulness-judged, the edit invalidates that verdict
    (the new text was never checked), so it is NOT auto-ready — it must be
    re-judged or explicitly approved. If no judge was ever in play, fall back to
    the normal deterministic rule.
    """
    if prev_had_judge:
        return False
    return email_is_ready(new_eval)
