import asyncio
import json
import time

from backend.agents import Agents
from backend.pipeline import (
    _clean_research_for_emails,
    _emailable_contacts,
    _fanout_contacts_and_research,
    _usable_contacts_for_emails,
    arun_pipeline,
    run_pipeline,
)


class _CannedAgent:
    """Inline fake: fixed response, or a prompt->response callable."""

    def __init__(self, response):
        self._response = response

    async def arun(self, prompt):
        content = self._response(prompt) if callable(self._response) else self._response
        return type("R", (), {"content": content})()

    def run(self, prompt):
        content = self._response(prompt) if callable(self._response) else self._response
        return type("R", (), {"content": content})()


def test_fanout_runs_all_companies_concurrently():
    """Per-company contacts + research (2 companies -> 4 tasks) must overlap."""

    class _SleepyAgent:
        def __init__(self, list_key):
            self._list_key = list_key

        async def arun(self, prompt):
            await asyncio.sleep(0.1)
            name = "Globex" if "Globex" in prompt else "Acme"
            payload = json.dumps({"name": name, self._list_key: ["x"]})
            return type("R", (), {"content": payload})()

        def run(self, prompt):  # unused here
            return type("R", (), {"content": "{}"})()

    agents = Agents(
        company_finder=_SleepyAgent("companies"),
        contact_finder=_SleepyAgent("contacts"),
        researcher=_SleepyAgent("insights"),
        email_writer=_SleepyAgent("emails"),
    )
    companies = [{"name": "Acme"}, {"name": "Globex"}]

    start = time.perf_counter()
    contacts, research = asyncio.run(
        _fanout_contacts_and_research(agents, companies, "t", "o", max_workers=4)
    )
    elapsed = time.perf_counter() - start

    # 4 tasks x 0.1s: concurrent under a pool of 4 finishes well under sequential 0.4s.
    assert elapsed < 0.18
    assert [c["name"] for c in contacts] == ["Acme", "Globex"]  # order preserved
    assert [r["name"] for r in research] == ["Acme", "Globex"]


def test_fanout_semaphore_bounds_concurrency():
    """With max_workers=1, the 4 tasks serialize -> ~0.4s."""

    class _SleepyAgent:
        async def arun(self, prompt):
            await asyncio.sleep(0.1)
            name = "Globex" if "Globex" in prompt else "Acme"
            return type("R", (), {"content": json.dumps({"name": name, "contacts": [], "insights": []})})()

        def run(self, prompt):
            return type("R", (), {"content": "{}"})()

    agents = Agents(_SleepyAgent(), _SleepyAgent(), _SleepyAgent(), _SleepyAgent())
    companies = [{"name": "Acme"}, {"name": "Globex"}]

    start = time.perf_counter()
    asyncio.run(_fanout_contacts_and_research(agents, companies, "t", "o", max_workers=1))
    elapsed = time.perf_counter() - start
    assert elapsed >= 0.4 - 0.02  # serialized


def test_usable_contacts_excludes_inferred_and_errors_by_default():
    data = [
        {"name": "Acme", "contacts": [
            {"full_name": "A", "email": "a@acme.com", "inferred": True},
            {"full_name": "B", "email": "b@acme.com", "inferred": False},
        ]},
        {"name": "Globex", "contacts": [], "error": "boom"},          # errored -> dropped
        {"name": "Initech", "contacts": [
            {"full_name": "C", "email": "c@initech.com", "inferred": True},  # only-inferred -> company dropped
        ]},
    ]
    out = _usable_contacts_for_emails(data, include_inferred=False)
    assert [r["name"] for r in out] == ["Acme"]
    assert out[0]["contacts"] == [{"full_name": "B", "email": "b@acme.com", "inferred": False}]
    assert "error" not in out[0]


def test_usable_contacts_can_include_inferred():
    data = [{"name": "Acme", "contacts": [{"full_name": "A", "email": "a@acme.com", "inferred": True}]}]
    out = _usable_contacts_for_emails(data, include_inferred=True)
    assert [r["name"] for r in out] == ["Acme"]


def test_clean_research_drops_errored_records():
    data = [{"name": "Acme", "insights": ["x"]}, {"name": "Globex", "insights": [], "error": "boom"}]
    assert _clean_research_for_emails(data) == [{"name": "Acme", "insights": ["x"]}]


def test_emailable_requires_nonempty_research():
    usable = [{"name": "Acme", "contacts": [1]}, {"name": "Globex", "contacts": [1]}]
    research = [{"name": "Acme", "insights": ["x"]}, {"name": "Globex", "insights": []}]
    out = _emailable_contacts(usable, research)
    assert [c["name"] for c in out] == ["Acme"]  # Globex has no insights -> no draft


def test_emailable_name_match_is_case_insensitive():
    usable = [{"name": "Acme Inc", "contacts": [1]}]
    research = [{"name": "acme inc", "insights": ["x"]}]
    assert len(_emailable_contacts(usable, research)) == 1


def test_pipeline_skips_email_when_company_has_no_research():
    """Globex has a verified contact but no research -> no email, but contact stays."""

    def contacts(prompt):
        name = "Globex" if "Globex" in prompt else "Acme"
        return json.dumps(
            {"name": name, "contacts": [{"full_name": f"{name} Chief", "email": f"c@{name.lower()}.com", "inferred": False}]}
        )

    def research(prompt):
        # Only Acme yields insights; Globex research comes back empty.
        insights = ["Acme did X"] if "Globex" not in prompt else []
        name = "Globex" if "Globex" in prompt else "Acme"
        return json.dumps({"name": name, "insights": insights})

    agents = Agents(
        company_finder=_CannedAgent(json.dumps({"companies": [{"name": "Acme"}, {"name": "Globex"}]})),
        contact_finder=_CannedAgent(contacts),
        researcher=_CannedAgent(research),
        # Prompt-aware: only drafts for companies actually present in the prompt,
        # so a leaked (un-gated) company would show up and fail the assertion.
        email_writer=_CannedAgent(
            lambda p: json.dumps(
                {"emails": [{"company": n, "contact": f"{n} Chief", "subject": "s", "body": "b"}
                            for n in ("Acme", "Globex") if n in p]}
            )
        ),
    )

    result = run_pipeline(
        target_desc="t", offering_desc="o", sender_name="S", sender_company="C",
        calendar_link=None, num_companies=2, agents=agents,
    )

    # Both contacts surfaced for the user...
    assert {c["name"] for c in result["contacts"]} == {"Acme", "Globex"}
    # ...but only Acme (which had research) got an email drafted.
    assert [e["company"] for e in result["emails"]] == ["Acme"]


def test_arun_pipeline_awaitable_inside_running_loop(fake_agents):
    """arun_pipeline must work when awaited from an already-running event loop."""

    async def caller():
        return await arun_pipeline(
            target_desc="t",
            offering_desc="o",
            sender_name="S",
            sender_company="C",
            calendar_link=None,
            num_companies=2,
            agents=fake_agents,
        )

    result = asyncio.run(caller())
    assert [c["name"] for c in result["companies"]] == ["Acme", "Globex"]
    assert result["emails"]


def test_job_postings_rescue_company_with_no_llm_research(monkeypatch):
    """Job-posting evidence augments research and can rescue a company whose
    website/Reddit research came back empty (so it becomes emailable)."""
    import backend.pipeline as pipeline
    from backend.models import Insight

    monkeypatch.setattr(
        pipeline,
        "fetch_job_insights",
        lambda name, **kw: [Insight(text=f"Hiring SRE at {name}", source_type="job_posting")],
    )

    agents = Agents(
        company_finder=_CannedAgent(json.dumps({"companies": [{"name": "Acme"}]})),
        contact_finder=_CannedAgent(
            json.dumps({"name": "Acme", "contacts": [{"full_name": "A", "email": "a@acme.com", "inferred": False}]})
        ),
        researcher=_CannedAgent(json.dumps({"name": "Acme", "insights": []})),  # empty LLM research
        email_writer=_CannedAgent(
            lambda p: json.dumps({"emails": [{"company": "Acme", "contact": "A", "subject": "s", "body": "b"}]})
        ),
    )

    result = run_pipeline(
        target_desc="t", offering_desc="o", sender_name="S", sender_company="C",
        calendar_link=None, num_companies=1, agents=agents,
    )

    assert any("Hiring SRE" in i for i in result["research"][0]["insights"])
    assert [e["company"] for e in result["emails"]] == ["Acme"]  # rescued -> emailable


def test_fanout_isolates_per_company_failure():
    """One company raising must not sink the batch; it yields an error record."""

    class _FlakyAgent:
        def __init__(self, list_key):
            self._list_key = list_key

        async def arun(self, prompt):
            if "Globex" in prompt:
                raise RuntimeError("boom")
            return type("R", (), {"content": json.dumps({"name": "Acme", self._list_key: ["ok"]})})()

        def run(self, prompt):
            return type("R", (), {"content": "{}"})()

    agents = Agents(_FlakyAgent("companies"), _FlakyAgent("contacts"), _FlakyAgent("insights"), _FlakyAgent("emails"))
    companies = [{"name": "Acme"}, {"name": "Globex"}]

    contacts, research = asyncio.run(
        _fanout_contacts_and_research(agents, companies, "t", "o", max_workers=4)
    )
    assert contacts[0]["contacts"] == ["ok"] and "error" in contacts[1]
    assert research[0]["insights"] == ["ok"] and "error" in research[1]


def test_run_pipeline_offline_with_mocked_agents(fake_agents):
    events = []
    result = run_pipeline(
        target_desc="B2B SaaS in the US",
        offering_desc="We sell observability tooling",
        sender_name="Sales Team",
        sender_company="Our Company",
        calendar_link=None,
        num_companies=2,
        email_style="Professional",
        agents=fake_agents,
        progress_cb=lambda *a: events.append(a),
    )

    assert [c["name"] for c in result["companies"]] == ["Acme", "Globex"]
    # Per-company fan-out: one contacts + one research record per company, in order.
    assert [c["name"] for c in result["contacts"]] == ["Acme", "Globex"]
    assert result["contacts"][0]["contacts"][0]["email"] == "ceo@acme.com"
    assert [r["name"] for r in result["research"]] == ["Acme", "Globex"]
    assert result["research"][1]["insights"] == ["Globex insight 1", "Globex insight 2"]
    assert result["emails"][0]["subject"] == "Quick idea"


def test_pipeline_respects_company_limit(fake_agents):
    result = run_pipeline(
        target_desc="t",
        offering_desc="o",
        sender_name="S",
        sender_company="C",
        calendar_link=None,
        num_companies=1,
        email_style="Professional",
        agents=fake_agents,
    )
    # Fake returns two companies; limit of 1 must slice it down.
    assert len(result["companies"]) == 1


def test_pipeline_emits_progress_for_each_stage(fake_agents):
    stages = []
    run_pipeline(
        target_desc="t",
        offering_desc="o",
        sender_name="S",
        sender_company="C",
        calendar_link=None,
        num_companies=2,
        email_style="Professional",
        agents=fake_agents,
        progress_cb=lambda stage, total, msg, detail: stages.append(stage),
    )
    assert set(stages) == {1, 2, 3, 4}


def test_pipeline_makes_no_network_calls(fake_agents):
    # Each agent is a FakeAgent; assert they were the ones invoked.
    run_pipeline(
        target_desc="t",
        offering_desc="o",
        sender_name="S",
        sender_company="C",
        calendar_link=None,
        num_companies=2,
        agents=fake_agents,
    )
    assert fake_agents.company_finder.calls
    assert fake_agents.contact_finder.calls
    assert fake_agents.researcher.calls
    assert fake_agents.email_writer.calls
