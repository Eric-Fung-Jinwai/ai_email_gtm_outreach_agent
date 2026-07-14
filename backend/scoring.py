"""Deterministic lead scoring (trimmed lead-scoring feature).

Prioritizes outreach with explainable, reproducible signals — contact seniority,
evidence strength, and draft readiness — NOT an LLM (personalization quality
already lives in the Phase 4c faithfulness/coverage judge). Half-deterministic on
purpose: defensible, sortable, and cheap.
"""

import re
from typing import Any, Dict, List, Tuple

# Title-keyword seniority rules, checked IN ORDER (first match wins). VP-style
# titles are checked before C-level so "Vice President" doesn't match "president".
_SENIORITY_RULES: List[Tuple[Tuple[str, ...], int]] = [
    (("vp", "vice president", "svp", "evp", "head of"), 4),
    (("founder", "ceo", "cto", "cfo", "coo", "chief", "president", "owner"), 5),
    (("director",), 3),
    (("principal", "lead", "manager", "senior"), 2),
]


def seniority_score(title: str) -> int:
    """0–5 seniority from a job title; 1 for IC/unknown.

    Matches on word boundaries so short acronyms don't hit substrings — e.g.
    "cto" must not match inside "direCTOr".
    """
    t = (title or "").lower()
    for keywords, score in _SENIORITY_RULES:
        if any(re.search(rf"\b{re.escape(k)}\b", t) for k in keywords):
            return score
    return 1


def lead_score(
    *, seniority: int, insight_count: int, has_job_evidence: bool, ready: bool, inferred: bool
) -> Dict[str, Any]:
    """Composite priority score with an explainable per-signal breakdown."""
    breakdown = {
        "seniority": seniority * 2,  # weight the decision-maker signal
        "evidence": min(insight_count, 4) + (3 if has_job_evidence else 0),
        "ready": 3 if ready else 0,
        "verified_email": 0 if inferred else 1,
    }
    return {"score": sum(breakdown.values()), "breakdown": breakdown}
