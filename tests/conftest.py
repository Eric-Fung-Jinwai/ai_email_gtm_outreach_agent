"""Mocked-agent test harness.

``FakeAgent`` mimics the only surface the pipeline uses (``run(prompt).content``)
and returns canned JSON, so the whole pipeline runs offline with zero API calls.
"""

# ENVIRONMENT now defaults to "production" (secure-by-default), which would make
# the web app reject the default session secret and mark session cookies Secure
# (unusable over http in TestClient). Tests are local development; pin that BEFORE
# any backend.config.Settings is instantiated/cached.
import os

os.environ.setdefault("ENVIRONMENT", "development")

import json

import pytest

from backend.agents import Agents


class _FakeRunOutput:
    def __init__(self, content: str) -> None:
        self.content = content


class FakeAgent:
    """Returns a canned response (str or prompt->str callable); records calls."""

    def __init__(self, response) -> None:
        self._response = response
        self.calls: list[str] = []

    def run(self, prompt: str) -> _FakeRunOutput:
        self.calls.append(prompt)
        return _FakeRunOutput(self._content(prompt))

    async def arun(self, prompt: str) -> _FakeRunOutput:
        self.calls.append(prompt)
        return _FakeRunOutput(self._content(prompt))

    def _content(self, prompt: str) -> str:
        return self._response(prompt) if callable(self._response) else self._response


def _json(obj) -> str:
    return json.dumps(obj)


@pytest.fixture(autouse=True)
def _no_network_job_fetch(monkeypatch):
    """Keep the suite hermetic: the pipeline's job-postings fetch never hits the
    real JSearch API during tests. Tests that want job evidence re-patch this.
    """
    import backend.pipeline as pipeline

    monkeypatch.setattr(pipeline, "fetch_job_insights", lambda name, **kwargs: [])


def _company_in_prompt(prompt: str) -> str:
    """Contacts/research are fanned out per company; detect which one from the prompt."""
    return "Globex" if "Globex" in prompt else "Acme"


def _contacts_response(prompt: str) -> str:
    name = _company_in_prompt(prompt)
    return _json(
        {
            "name": name,
            "contacts": [
                {"full_name": f"{name} Chief", "title": "CEO", "email": f"ceo@{name.lower()}.com", "inferred": False}
            ],
        }
    )


def _research_response(prompt: str) -> str:
    name = _company_in_prompt(prompt)
    return _json({"name": name, "insights": [f"{name} insight 1", f"{name} insight 2"]})


@pytest.fixture
def fake_agents() -> Agents:
    """Two-company canned dataset wired into a full ``Agents`` container.

    Contacts/research fakes are prompt-aware so per-company fan-out returns the
    right company's data on each call.
    """
    companies = {
        "companies": [
            {"name": "Acme", "website": "https://acme.com", "why_fit": "Great fit A"},
            {"name": "Globex", "website": "https://globex.com", "why_fit": "Great fit B"},
        ]
    }
    emails = {
        "emails": [
            {"company": "Acme", "contact": "Acme Chief", "subject": "Quick idea", "body": "Hello, ..."}
        ]
    }
    return Agents(
        company_finder=FakeAgent(_json(companies)),
        contact_finder=FakeAgent(_contacts_response),
        researcher=FakeAgent(_research_response),
        email_writer=FakeAgent(_json(emails)),
    )
