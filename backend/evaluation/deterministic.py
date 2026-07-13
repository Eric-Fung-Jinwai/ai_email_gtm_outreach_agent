"""Deterministic email-quality checks.

The free, fully-testable gate: no network, no model calls. Runs *before* any
(paid, slower) LLM judge so obviously-broken drafts are caught cheaply and the
judge only sees emails worth the tokens.
"""

import re
from typing import Any, Dict, List, Optional

from backend.config import Settings, get_settings
from backend.models import CheckResult, EvalResult

# Cues that indicate a call-to-action.
_CTA_CUES = [
    "call", "chat", "meeting", "meet", "connect", "demo", "calendar", "book",
    "schedule", "reply", "intro", "discuss", "catch up", "hop on", "quick word",
]

# Spammy phrases/patterns that hurt deliverability and credibility.
_SPAM_PHRASES = [
    "act now", "limited time", "free money", "100% free", "risk-free", "risk free",
    "buy now", "click here", "cash bonus", "no obligation", "winner",
    "congratulations you", "guaranteed", "make money fast", "lowest price",
    "order now", "this is not spam", "dear friend",
]


def _word_count(text: str) -> int:
    return len(re.findall(r"\b\w[\w'-]*\b", text or ""))


def _check_subject(email: Dict[str, Any]) -> CheckResult:
    ok = bool((email.get("subject") or "").strip())
    return CheckResult(name="subject_present", passed=ok, detail="" if ok else "missing subject")


def _check_body(email: Dict[str, Any]) -> CheckResult:
    ok = bool((email.get("body") or "").strip())
    return CheckResult(name="body_present", passed=ok, detail="" if ok else "empty body")


def _check_word_count(email: Dict[str, Any], min_words: int, max_words: int) -> CheckResult:
    n = _word_count(email.get("body") or "")
    ok = min_words <= n <= max_words
    return CheckResult(
        name="word_count", passed=ok, detail=f"{n} words (allowed {min_words}-{max_words})"
    )


def _check_cta(email: Dict[str, Any]) -> CheckResult:
    body = (email.get("body") or "").lower()
    ok = "?" in body or any(cue in body for cue in _CTA_CUES)
    return CheckResult(name="cta_present", passed=ok, detail="" if ok else "no clear call-to-action")


def _check_spam(email: Dict[str, Any]) -> CheckResult:
    blob = f"{email.get('subject', '')} {email.get('body', '')}".lower()
    hits = [p for p in _SPAM_PHRASES if p in blob]
    if "!!!" in blob or "$$$" in blob:
        hits.append("excessive punctuation")
    ok = not hits
    return CheckResult(name="no_spam", passed=ok, detail="" if ok else f"spam signals: {', '.join(hits)}")


def _check_calendar(email: Dict[str, Any], calendar_link: str) -> CheckResult:
    ok = calendar_link in (email.get("body") or "")
    return CheckResult(
        name="calendar_link_included", passed=ok, detail="" if ok else "calendar link missing"
    )


def evaluate_email(
    email: Dict[str, Any],
    *,
    calendar_link: Optional[str] = None,
    settings: Optional[Settings] = None,
) -> EvalResult:
    """Run all deterministic checks on one email. Pure function, never raises."""
    settings = settings or get_settings()
    checks: List[CheckResult] = [
        _check_subject(email),
        _check_body(email),
        _check_word_count(email, settings.email_min_words, settings.email_max_words),
        _check_cta(email),
        _check_spam(email),
    ]
    if calendar_link:  # only enforce the link when the sender actually provided one
        checks.append(_check_calendar(email, calendar_link))
    passed = all(c.passed for c in checks)
    # ready == passed until/unless an LLM judge later overrides it (see pipeline).
    return EvalResult(passed=passed, checks=checks, ready=passed)
