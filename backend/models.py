"""Typed data models shared across the backend."""

from typing import Optional

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
