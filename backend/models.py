"""Typed data models shared across the backend."""

from typing import List, Optional

from pydantic import BaseModel


class Insight(BaseModel):
    """A single unit of grounding evidence for personalization.

    ``source_type`` is one of {website, reddit, job_posting}. Carrying the source
    URL/type (rather than a bare string) is what lets the Phase 4 faithfulness
    eval check that each email claim traces back to real retrieved evidence.
    """

    text: str
    source_url: Optional[str] = None
    source_type: str = "insight"


class Email(BaseModel):
    """A generated outreach email. Fields default to empty so a partial/malformed
    model response coerces into a safe record rather than crashing the pipeline.
    """

    company: str = ""
    contact: str = ""
    subject: str = ""
    body: str = ""


class CheckResult(BaseModel):
    """One deterministic check outcome."""

    name: str
    passed: bool
    detail: str = ""


class ClaimVerdict(BaseModel):
    """One factual claim in an email and whether it traces to retrieved evidence."""

    claim: str
    grounded: bool
    evidence: Optional[str] = None  # the evidence text/URL it maps to, if any


class JudgeVerdict(BaseModel):
    """Grounded LLM-judge output for one email (RAG-faithfulness + quality).

    ``faithful`` = every factual company-claim traces to evidence. ``coverage`` /
    ``personalization`` are coarse labels (high|medium|low). ``error`` is set if
    the judge call itself failed (so it never silently marks an email unfaithful).
    """

    faithful: Optional[bool] = None
    claims: List[ClaimVerdict] = []
    coverage: str = "unknown"
    personalization: str = "unknown"
    issues: List[str] = []
    error: Optional[str] = None


class EvalResult(BaseModel):
    """Aggregate evaluation for a single email.

    ``checks`` is the deterministic gate (Phase 4b). ``judge`` is the grounded
    LLM-judge (Phase 4c), populated only for emails that passed the deterministic
    gate — so the paid judge never runs on obviously-broken drafts.
    """

    passed: bool  # deterministic gate only
    checks: List[CheckResult] = []
    judge: Optional[JudgeVerdict] = None
    ready: bool = False  # overall: deterministic pass AND (no judge or faithful)


class GoldenExample(BaseModel):
    """One hand-labeled example for validating the faithfulness judge."""

    id: str
    email: Email
    evidence: List[Insight] = []
    label_faithful: bool  # the human ground-truth label
    note: str = ""


class AgreementReport(BaseModel):
    """Judge↔human agreement over the golden set.

    Positive class = *unfaithful* (i.e. detecting a hallucination), so precision/
    recall describe how well the judge catches ungrounded emails.
    """

    total: int
    evaluated: int  # excludes judge errors
    errors: int
    agreements: int
    accuracy: float
    true_pos: int
    false_pos: int
    true_neg: int
    false_neg: int
    precision: float
    recall: float
