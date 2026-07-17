"""Tests for contact validation (#4), draft binding (#3), and list coercion (#5)."""

import json

import pytest

from backend.agents import Agents
from backend.evaluation.gating import email_is_ready
from backend.pipeline import _usable_contacts_for_emails, run_pipeline
from backend.text_utils import canonical_host, email_domain, is_valid_email, safe_http_url


class _Canned:
    def __init__(self, response):
        self._response = response

    async def arun(self, prompt):
        content = self._response(prompt) if callable(self._response) else self._response
        return type("R", (), {"content": content})()

    def run(self, prompt):
        return type("R", (), {"content": self._response})()


# --- validation helpers ---

def test_email_and_host_helpers():
    assert is_valid_email("ada@acme.com") and not is_valid_email("nope")
    assert not is_valid_email("") and not is_valid_email("a@b")
    assert canonical_host("https://www.acme.com/team") == "acme.com"
    assert canonical_host("acme.com") == "acme.com"
    assert canonical_host("") is None
    assert email_domain("ada@www.acme.com") == "acme.com"


def test_safe_http_url_blocks_dangerous_schemes():
    assert safe_http_url("https://example.com/x") == "https://example.com/x"
    assert safe_http_url("http://example.com") == "http://example.com"
    assert safe_http_url("javascript:alert(1)") is None
    assert safe_http_url("data:text/html;base64,PHNjcmlwdD4=") is None
    assert safe_http_url("  javascript:alert(1)  ") is None  # whitespace can't smuggle it
    assert safe_http_url(None) is None
    assert safe_http_url(123) is None


# --- #4: contact validation ---

def _rec(contacts):
    return [{"name": "Acme", "contacts": contacts}]


def test_invalid_email_contact_dropped():
    out = _usable_contacts_for_emails(_rec([{"full_name": "A", "email": "garbage", "inferred": False}]), False)
    assert out == []


def test_missing_inferred_flag_excluded_by_default():
    out = _usable_contacts_for_emails(_rec([{"full_name": "A", "email": "a@acme.com"}]), False)
    assert out == []  # missing inferred == inferred -> excluded


def test_explicit_non_inferred_valid_email_kept():
    out = _usable_contacts_for_emails(_rec([{"full_name": "A", "email": "a@acme.com", "inferred": False}]), False)
    assert [c["full_name"] for c in out[0]["contacts"]] == ["A"]


def test_domain_mismatch_is_soft_flag_not_rejection():
    data = _rec([{"full_name": "A", "email": "a@gmail.com", "inferred": False}])
    out = _usable_contacts_for_emails(data, False, {"acme": "acme.com"})
    assert out[0]["contacts"][0]["domain_mismatch"] is True  # kept, flagged
    # matching domain -> not flagged
    ok = _usable_contacts_for_emails(_rec([{"full_name": "A", "email": "a@acme.com", "inferred": False}]), False, {"acme": "acme.com"})
    assert ok[0]["contacts"][0]["domain_mismatch"] is False


def test_non_dict_contact_element_coerced_out():
    out = _usable_contacts_for_emails(_rec(["oops", {"full_name": "A", "email": "a@acme.com", "inferred": False}]), False)
    assert [c["full_name"] for c in out[0]["contacts"]] == ["A"]


# --- #5: malformed company/contact lists don't crash the run ---

def test_pipeline_survives_non_dict_company_and_contact_elements():
    agents = Agents(
        company_finder=_Canned(json.dumps({"companies": ["oops", {"name": "Acme"}]})),
        contact_finder=_Canned(json.dumps({"name": "Acme", "contacts": [None, {"full_name": "A", "email": "a@acme.com", "inferred": False}]})),
        researcher=_Canned(json.dumps({"name": "Acme", "insights": ["hiring"]})),
        email_writer=_Canned(json.dumps({"emails": []})),
    )
    # Must not raise despite the string company and null contact.
    result = run_pipeline(
        target_desc="t", offering_desc="o", sender_name="S", sender_company="C",
        calendar_link=None, num_companies=2, agents=agents,
    )
    assert [c["name"] for c in result["companies"]] == ["Acme"]


def test_pipeline_survives_null_and_non_list_containers():
    """{"companies": null} / non-list containers must not crash iteration."""
    agents = Agents(
        company_finder=_Canned(json.dumps({"companies": None})),  # null container
        contact_finder=_Canned(json.dumps({"name": "Acme", "contacts": "oops"})),  # non-list
        researcher=_Canned(json.dumps({"name": "Acme", "insights": None})),
        email_writer=_Canned(json.dumps({"emails": None})),
    )
    result = run_pipeline(
        target_desc="t", offering_desc="o", sender_name="S", sender_company="C",
        calendar_link=None, num_companies=2, agents=agents,
    )
    assert result["companies"] == [] and result["emails"] == []


# --- #3: drafts bound to the approved contact set ---

def test_unbound_recipient_is_flagged_not_ready():
    good_body = (
        "Hi, I noticed Acme is hiring backend engineers, which usually signals scaling "
        "pressure. We help teams in that spot cut infra toil and ship faster without adding "
        "headcount. Would you be open to a short intro call next week to discuss the details?"
    )
    agents = Agents(
        company_finder=_Canned(json.dumps({"companies": [{"name": "Acme"}]})),
        contact_finder=_Canned(json.dumps({"name": "Acme", "contacts": [{"full_name": "Ada", "email": "ada@acme.com", "inferred": False}]})),
        researcher=_Canned(json.dumps({"name": "Acme", "insights": ["Acme is hiring"]})),
        # Writer emits an email for a company/contact we never supplied.
        email_writer=_Canned(json.dumps({"emails": [{"company": "Evil Corp", "contact": "Nobody", "subject": "Hi there", "body": good_body}]})),
    )
    result = run_pipeline(
        target_desc="t", offering_desc="o", sender_name="S", sender_company="C",
        calendar_link=None, num_companies=1, agents=agents,
    )
    e = result["emails"][0]
    assert e["eval"]["binding_error"]
    assert email_is_ready(e["eval"]) is False  # never in the ready queue
