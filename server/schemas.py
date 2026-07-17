"""Request/response models for the API. Validation lives here (point 10)."""

import re
from typing import List, Optional

from pydantic import BaseModel, Field, field_validator

# Keep in sync with backend.agents.get_email_style_instruction.
EMAIL_STYLES = ["Professional", "Casual", "Cold", "Consultative"]

_USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{3,32}$")


class Credentials(BaseModel):
    username: str = Field(min_length=3, max_length=32)
    password: str = Field(min_length=8, max_length=128)

    @field_validator("username")
    @classmethod
    def _valid_username(cls, v: str) -> str:
        v = v.strip()
        if not _USERNAME_RE.match(v):
            raise ValueError("username must be 3-32 chars: letters, digits, . _ -")
        return v


class UserOut(BaseModel):
    id: int
    username: str


class RunRequest(BaseModel):
    target_desc: str = Field(min_length=1, max_length=2000)
    offering_desc: str = Field(min_length=1, max_length=2000)
    sender_name: str = Field(default="Sales Team", max_length=200)
    sender_company: str = Field(default="Our Company", max_length=200)
    calendar_link: Optional[str] = Field(default=None, max_length=500)
    num_companies: int = Field(default=5, ge=1, le=10)
    email_style: str = "Professional"

    @field_validator("target_desc", "offering_desc")
    @classmethod
    def _non_blank(cls, v: str) -> str:
        # Reject whitespace-only input that would slip past min_length then become
        # empty after .strip() in the pipeline.
        if not v.strip():
            raise ValueError("must not be blank")
        return v

    @field_validator("email_style")
    @classmethod
    def _valid_style(cls, v: str) -> str:
        if v not in EMAIL_STYLES:
            raise ValueError(f"email_style must be one of {EMAIL_STYLES}")
        return v


class RunStarted(BaseModel):
    job_id: str


class TransitionRequest(BaseModel):
    action: str
    # Optimistic-lock token the client last saw. REQUIRED for API clients so no one
    # can omit it and silently restore last-write-wins; conflict → 409. (The legacy
    # Streamlit/internal path passes None straight to persistence, bypassing this.)
    expected_version: int = Field(ge=0)

    @field_validator("action")
    @classmethod
    def _valid_action(cls, v: str) -> str:
        if v not in {"approve", "reject"}:
            raise ValueError("action must be 'approve' or 'reject'")
        return v


class EditRequest(BaseModel):
    subject: str = Field(max_length=500)
    body: str = Field(max_length=20000)
    expected_version: int = Field(ge=0)  # required — see TransitionRequest


class ConfigOut(BaseModel):
    has_openai: bool
    has_exa: bool
    email_styles: List[str] = EMAIL_STYLES
    max_companies: int = 10
