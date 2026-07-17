"""API/integration tests for the multi-user FastAPI frontend.

Runs fully offline: a temp DB and fake agents are injected via
``app.dependency_overrides`` (never touches tmp/campaigns.db or the network).

NOTE: the SSE run flow relies on a background asyncio task created during POST
/runs, so tests MUST use ``with TestClient(app) as c:`` — that keeps a single
event loop alive across requests so the task can make progress.
"""

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from backend.agents import Agents
from server import deps
from server import runs as runs_mod
from server.main import app
from server.ratelimit import _AUTH_LIMITER

# A body that clears the deterministic gate.
GOOD_BODY = (
    "Hi Ada, I noticed Acme has been hiring backend engineers, which usually signals "
    "real scaling pressure on the platform team. We help companies in exactly that spot "
    "cut infrastructure toil and ship features faster without adding headcount. Would you "
    "be open to a short intro call next week so I can walk through what that looked like?"
)


class _Canned:
    def __init__(self, response):
        self._response = response

    async def arun(self, prompt):
        return type("R", (), {"content": self._response})()

    def run(self, prompt):
        return type("R", (), {"content": self._response})()


def _fake_agents(_style):
    return Agents(
        company_finder=_Canned(json.dumps({"companies": [{"name": "Acme", "website": "acme.com"}]})),
        contact_finder=_Canned(json.dumps({"name": "Acme", "contacts": [
            {"full_name": "Ada", "title": "VP Eng", "email": "ada@acme.com", "inferred": False}]})),
        researcher=_Canned(json.dumps({"name": "Acme", "insights": ["Acme is hiring backend engineers"]})),
        email_writer=_Canned(json.dumps({"emails": [
            {"company": "Acme", "contact": "Ada", "subject": "Quick idea for Acme", "body": GOOD_BODY}]})),
    )


@pytest.fixture
def client(tmp_path):
    db = str(tmp_path / "campaigns.db")
    app.dependency_overrides[deps.get_db_path] = lambda: db
    app.dependency_overrides[deps.get_agents_factory] = lambda: _fake_agents
    runs_mod._JOBS.clear()  # isolate the in-memory job registry between tests
    _AUTH_LIMITER.reset()   # and the shared auth rate limiter
    with TestClient(app) as c:
        yield c
    runs_mod._JOBS.clear()
    _AUTH_LIMITER.reset()
    app.dependency_overrides.clear()


def _register(c, username="alice", password="password123"):
    return c.post("/api/auth/register", json={"username": username, "password": password})


def _payload(**over):
    base = {"target_desc": "saas", "offering_desc": "infra", "num_companies": 1, "email_style": "Professional"}
    base.update(over)
    return base


def _run_to_completion(c):
    """Start a run, drain the SSE stream, return the terminal event."""
    r = c.post("/api/runs", json=_payload())
    assert r.status_code == 200, r.text
    job_id = r.json()["job_id"]
    stream = c.get(f"/api/runs/stream/{job_id}")
    events = [json.loads(l[6:]) for l in stream.text.splitlines() if l.startswith("data: ")]
    return events


# --- auth -------------------------------------------------------------------

def test_auth_required(client):
    assert client.get("/api/runs").status_code == 401
    assert client.get("/api/config").status_code == 401
    assert client.get("/api/auth/me").status_code == 401


def test_register_login_logout(client):
    assert _register(client).status_code == 200
    assert client.get("/api/auth/me").json()["username"] == "alice"
    assert client.post("/api/auth/logout").status_code == 200
    assert client.get("/api/auth/me").status_code == 401
    # Log back in.
    assert client.post("/api/auth/login", json={"username": "alice", "password": "password123"}).status_code == 200
    assert client.get("/api/auth/me").json()["username"] == "alice"


def test_login_wrong_password_generic_401(client):
    _register(client)
    client.post("/api/auth/logout")
    r = client.post("/api/auth/login", json={"username": "alice", "password": "nope-nope"})
    assert r.status_code == 401 and "invalid credentials" in r.json()["detail"]


def test_duplicate_register_conflict(client):
    _register(client)
    assert _register(client).status_code == 409


def test_session_switches_identity(client):
    # Registering a second user must not leave the first identity in the session.
    _register(client, "alice")
    _register(client, "bob")
    assert client.get("/api/auth/me").json()["username"] == "bob"


# --- validation -------------------------------------------------------------

def test_run_input_validation(client):
    _register(client)
    assert client.post("/api/runs", json=_payload(num_companies=99)).status_code == 422
    assert client.post("/api/runs", json=_payload(email_style="Nope")).status_code == 422


def test_blank_whitespace_input_rejected(client):
    _register(client)
    # Whitespace-only passes min_length but is blank after strip → 422.
    assert client.post("/api/runs", json=_payload(target_desc="   ")).status_code == 422
    assert client.post("/api/runs", json=_payload(offering_desc="\t\n")).status_code == 422


def test_auth_rate_limit_returns_429(client):
    # 10 attempts/min per client IP; the 11th is throttled (protects PBKDF2 CPU).
    codes = [client.post("/api/auth/login", json={"username": "ghost", "password": "password123"}).status_code
             for _ in range(11)]
    assert codes[:10] == [401] * 10
    assert codes[10] == 429


def test_decorate_run_scrubs_dangerous_urls():
    run = {"emails": [], "research": [{"name": "Acme", "insights": [
        {"text": "x", "source_url": "javascript:alert(1)", "source_type": "website"},
        {"text": "y", "source_url": "https://ok.example", "source_type": "website"},
    ]}]}
    out = runs_mod._decorate_run(run)
    assert [i["source_url"] for i in out["research"][0]["insights"]] == [None, "https://ok.example"]


def test_missing_keys_returns_400(client):
    _register(client)

    def _raises(_style):
        raise RuntimeError("Missing required API key(s): OPENAI_API_KEY")

    app.dependency_overrides[deps.get_agents_factory] = lambda: _raises
    r = client.post("/api/runs", json=_payload())
    assert r.status_code == 400 and "OPENAI_API_KEY" in r.json()["detail"]


# --- run end-to-end ---------------------------------------------------------

def test_run_stream_and_reopen(client):
    _register(client)
    events = _run_to_completion(client)
    assert [e["type"] for e in events][-1] == "done"
    assert any(e["type"] == "progress" for e in events)
    run_id = events[-1]["run_id"]

    run = client.get(f"/api/runs/{run_id}").json()
    e = run["emails"][0]
    assert (e["company"], e["contact"]) == ("Acme", "Ada")
    assert e["ready"] is True

    # Transition + edit endpoints (expected_version is required and threaded through).
    t = client.post(f"/api/runs/{run_id}/emails/{e['id']}/transition",
                    json={"action": "approve", "expected_version": e["version"]})
    assert t.status_code == 200 and t.json()["status"] == "approved"

    ed = client.post(f"/api/runs/{run_id}/emails/{e['id']}/edit",
                     json={"subject": "Edited", "body": GOOD_BODY, "expected_version": t.json()["version"]})
    assert ed.status_code == 200 and ed.json()["status"] == "edited"

    # edited -> reject is legal (sanity that the endpoint works with the fresh version).
    ok = client.post(f"/api/runs/{run_id}/emails/{e['id']}/transition",
                     json={"action": "reject", "expected_version": ed.json()["version"]})
    assert ok.status_code == 200

    # Omitting expected_version is rejected — no client can bypass optimistic locking.
    missing = client.post(f"/api/runs/{run_id}/emails/{e['id']}/transition", json={"action": "approve"})
    assert missing.status_code == 422


def test_edit_version_conflict_409(client):
    _register(client)
    run_id = _run_to_completion(client)[-1]["run_id"]
    e = client.get(f"/api/runs/{run_id}").json()["emails"][0]
    v = e["version"]
    ok = client.post(f"/api/runs/{run_id}/emails/{e['id']}/edit",
                     json={"subject": "A", "body": GOOD_BODY, "expected_version": v})
    assert ok.status_code == 200 and ok.json()["version"] == v + 1
    # A second tab still holding the stale version is refused, not silently clobbering.
    conflict = client.post(f"/api/runs/{run_id}/emails/{e['id']}/edit",
                           json={"subject": "B", "body": GOOD_BODY, "expected_version": v})
    assert conflict.status_code == 409
    assert client.get(f"/api/runs/{run_id}").json()["emails"][0]["subject"] == "A"


def test_pipeline_timeout_emits_error(client, monkeypatch):
    from backend.config import get_settings

    monkeypatch.setattr(get_settings(), "pipeline_timeout_seconds", 0.2)
    _register(client)
    hang = asyncio.Event()  # never set → the run must hit the deadline

    class _Hang(_Canned):
        async def arun(self, prompt):
            await hang.wait()
            return type("R", (), {"content": "{}"})()

    def _hanging_agents(_style):
        a = _fake_agents(_style)
        a.company_finder = _Hang("{}")
        return a

    app.dependency_overrides[deps.get_agents_factory] = lambda: _hanging_agents
    events = _run_to_completion(client)
    assert events[-1]["type"] == "error"
    assert "timed out" in events[-1]["message"]


def test_list_and_cost_scoped(client):
    _register(client)
    events = _run_to_completion(client)
    assert events[-1]["type"] == "done"
    data = client.get("/api/runs").json()
    assert len(data["runs"]) == 1
    assert "total_cost" in data


# --- tenant isolation -------------------------------------------------------

def test_cross_user_run_is_404(client):
    _register(client, "alice")
    run_id = _run_to_completion(client)[-1]["run_id"]
    client.post("/api/auth/logout")
    _register(client, "bob")
    assert client.get(f"/api/runs/{run_id}").status_code == 404
    assert client.post(f"/api/runs/{run_id}/emails/email-0/transition",
                       json={"action": "approve", "expected_version": 0}).status_code == 404
    assert client.get("/api/runs").json()["runs"] == []  # bob sees nothing


# --- CSRF / origin guard ----------------------------------------------------

def test_cross_origin_post_rejected(client):
    _register(client)
    r = client.post("/api/runs", json=_payload(), headers={"Origin": "http://evil.example"})
    assert r.status_code == 403


# --- concurrency cap --------------------------------------------------------

def test_active_job_cap_returns_429(client):
    _register(client)
    hang = asyncio.Event()  # never set → jobs stay "running"

    class _Hang(_Canned):
        async def arun(self, prompt):
            await hang.wait()
            return type("R", (), {"content": "{}"})()

    def _hanging_agents(_style):
        a = _fake_agents(_style)
        a.company_finder = _Hang("{}")
        return a

    app.dependency_overrides[deps.get_agents_factory] = lambda: _hanging_agents
    # Per-user cap is 2 → first two accepted, third rejected.
    assert client.post("/api/runs", json=_payload()).status_code == 200
    assert client.post("/api/runs", json=_payload()).status_code == 200
    assert client.post("/api/runs", json=_payload()).status_code == 429
