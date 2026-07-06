"""Outreach pipeline orchestration.

Pure backend: no Streamlit import. Stage progress is surfaced via an optional
``progress_cb`` callback so any frontend can render it. Agents are injected via
the ``Agents`` container, which makes the whole pipeline testable with fakes.

Contacts and research are fanned out **per company** and run concurrently via
``agent.arun`` + ``asyncio.gather`` under a bounded semaphore. This is only safe
because those agents are stateless (see ``agents.py``). Benefits over the old
per-stage batching: finer-grained parallelism, per-company cacheability
(Phase 8), and per-company failure isolation.
"""

import asyncio
import json
from typing import Any, Callable, Dict, List, Optional, Tuple

from backend.agents import Agents, AgentLike, build_agents
from backend.config import get_settings
from backend.json_utils import extract_json_or_raise
from backend.sources.jsearch import fetch_job_insights

# progress_cb(stage: int, total: int, message: str, detail: str) -> None
ProgressCb = Optional[Callable[[int, int, str, str], None]]

_TOTAL_STAGES = 4


def _emit(cb: ProgressCb, stage: int, message: str, detail: str = "") -> None:
    if cb is not None:
        cb(stage, _TOTAL_STAGES, message, detail)


def _parse(resp: Any) -> Dict[str, Any]:
    return extract_json_or_raise(str(resp.content))


def _extract_single(company: Dict[str, str], data: Dict[str, Any], list_key: str) -> Dict[str, Any]:
    """Normalize a single-company agent response to ``{name, <list_key>}``.

    Tolerates a model that wraps the answer in ``{"companies": [ ... ]}`` even
    though we asked for a single company.
    """
    inner = data
    if isinstance(data.get("companies"), list) and data["companies"]:
        inner = data["companies"][0]
    return {
        "name": inner.get("name") or company.get("name", ""),
        list_key: inner.get(list_key) or [],
    }


# --- Prompt builders ---

def _company_finder_prompt(target_desc: str, offering_desc: str, max_companies: int) -> str:
    return (
        f"Find exactly {max_companies} companies that are a strong B2B fit given the user inputs.\n"
        f"Targeting: {target_desc}\n"
        f"Offering: {offering_desc}\n"
        "For each, provide: name, website, why_fit (1-2 lines)."
    )


def _contact_finder_prompt(company: Dict[str, str], target_desc: str, offering_desc: str) -> str:
    return (
        "For the single company below, find 2-3 relevant decision makers and emails (if available). "
        "Ensure at least 2 when possible, and cap at 3.\n"
        "If not available, infer likely email and mark inferred=true.\n"
        f"Targeting: {target_desc}\nOffering: {offering_desc}\n"
        f"Company JSON: {json.dumps(company, ensure_ascii=False)}\n"
        "Return JSON: {name, contacts: [{full_name, title, email, inferred}]}"
    )


def _research_prompt(company: Dict[str, str]) -> str:
    return (
        "For the single company below, gather 2-4 interesting insights from their website and Reddit "
        "that would help personalize outreach.\n"
        f"Company JSON: {json.dumps(company, ensure_ascii=False)}\n"
        "Return JSON: {name, insights: [string, ...]}"
    )


def _email_writer_prompt(
    contacts_data: List[Dict[str, Any]],
    research_data: List[Dict[str, Any]],
    offering_desc: str,
    sender_name: str,
    sender_company: str,
    calendar_link: Optional[str],
) -> str:
    return (
        "Write personalized outreach emails for the following contacts.\n"
        f"Sender: {sender_name} at {sender_company}.\n"
        f"Offering: {offering_desc}.\n"
        f"Calendar link: {calendar_link or 'N/A'}.\n"
        f"Contacts JSON: {json.dumps(contacts_data, ensure_ascii=False)}\n"
        f"Research JSON: {json.dumps(research_data, ensure_ascii=False)}\n"
        "Return JSON with key 'emails' as a list of {company, contact, subject, body}."
    )


# --- Async single-shot stage runners ---

async def _arun_company_finder(
    agent: AgentLike, target_desc: str, offering_desc: str, max_companies: int
) -> List[Dict[str, str]]:
    resp = await agent.arun(_company_finder_prompt(target_desc, offering_desc, max_companies))
    companies = _parse(resp).get("companies", [])
    return companies[: max(1, min(max_companies, 10))]


async def _arun_email_writer(
    agent: AgentLike,
    contacts_data: List[Dict[str, Any]],
    research_data: List[Dict[str, Any]],
    offering_desc: str,
    sender_name: str,
    sender_company: str,
    calendar_link: Optional[str],
) -> List[Dict[str, str]]:
    resp = await agent.arun(
        _email_writer_prompt(
            contacts_data, research_data, offering_desc, sender_name, sender_company, calendar_link
        )
    )
    return _parse(resp).get("emails", [])


# --- Filtering: only feed the email writer trustworthy, complete evidence ---

def _usable_contacts_for_emails(
    contacts_data: List[Dict[str, Any]], include_inferred: bool
) -> List[Dict[str, Any]]:
    """Records safe to draft emails from.

    Drops per-company error records (also keeps raw exception text out of the
    prompt) and companies with no contacts, and — unless ``include_inferred`` —
    drops inferred (guessed) email contacts.
    """
    usable: List[Dict[str, Any]] = []
    for rec in contacts_data:
        if rec.get("error"):
            continue
        contacts = rec.get("contacts") or []
        if not include_inferred:
            contacts = [c for c in contacts if not c.get("inferred")]
        if contacts:
            cleaned = {k: v for k, v in rec.items() if k != "error"}
            cleaned["contacts"] = contacts
            usable.append(cleaned)
    return usable


def _clean_research_for_emails(research_data: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Drop errored research records so exception text never reaches the prompt."""
    return [r for r in research_data if not r.get("error")]


def _norm_name(name: str) -> str:
    return (name or "").strip().lower()


def _emailable_contacts(
    usable_contacts: List[Dict[str, Any]], clean_research: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """Contacts we will actually draft emails for.

    Policy: only companies that have **non-empty research insights**. Verified
    contacts at companies without research are intentionally left for manual
    outreach (surfaced in the UI) rather than drafted with no evidence — which
    would force the writer to omit personalization or hallucinate it.
    """
    researched = {
        _norm_name(r.get("name", "")) for r in clean_research if (r.get("insights") or [])
    }
    return [c for c in usable_contacts if _norm_name(c.get("name", "")) in researched]


# --- Per-company async workers (contacts + research fan-out) ---

async def _contacts_for_company(
    agent: AgentLike,
    company: Dict[str, str],
    target_desc: str,
    offering_desc: str,
    sem: asyncio.Semaphore,
) -> Dict[str, Any]:
    async with sem:
        try:
            resp = await agent.arun(_contact_finder_prompt(company, target_desc, offering_desc))
            return _extract_single(company, _parse(resp), "contacts")
        except Exception as e:  # isolate failure to this company
            return {"name": company.get("name", ""), "contacts": [], "error": str(e)}


async def _research_for_company(
    agent: AgentLike, company: Dict[str, str], sem: asyncio.Semaphore
) -> Dict[str, Any]:
    async with sem:
        name = company.get("name", "")
        try:
            resp = await agent.arun(_research_prompt(company))
            record = _extract_single(company, _parse(resp), "insights")
        except Exception as e:  # isolate LLM failure to this company
            record = {"name": name, "insights": [], "error": str(e)}

        # Augment with deterministic job-posting evidence (best-effort). Run the
        # sync httpx call in a thread so it doesn't block the event loop. Job
        # postings alone count as evidence, so they can rescue a company whose
        # website/Reddit research failed.
        job_texts = [i.text for i in await asyncio.to_thread(fetch_job_insights, name)]
        if job_texts:
            record["insights"] = list(record.get("insights") or []) + job_texts
            record.pop("error", None)
        return record


async def _fanout_contacts_and_research(
    agents: Agents,
    companies: List[Dict[str, str]],
    target_desc: str,
    offering_desc: str,
    max_workers: int,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Fan out contacts + research per company, bounded by ``max_workers``.

    Results preserve input company order (``asyncio.gather`` is order-preserving).
    """
    sem = asyncio.Semaphore(max(1, max_workers))
    contact_coros = [
        _contacts_for_company(agents.contact_finder, c, target_desc, offering_desc, sem)
        for c in companies
    ]
    research_coros = [_research_for_company(agents.researcher, c, sem) for c in companies]
    results = await asyncio.gather(*contact_coros, *research_coros)
    n = len(companies)
    return list(results[:n]), list(results[n:])


async def arun_pipeline(
    *,
    target_desc: str,
    offering_desc: str,
    sender_name: str,
    sender_company: str,
    calendar_link: Optional[str],
    num_companies: int,
    email_style: str = "Professional",
    agents: Optional[Agents] = None,
    progress_cb: ProgressCb = None,
    include_inferred_contacts: Optional[bool] = None,
) -> Dict[str, Any]:
    """Async 4-stage outreach pipeline. Safe to ``await`` from any event loop
    (FastAPI, async workers, notebooks).

    If ``agents`` is omitted, real agno-backed agents are built (requires API
    keys in the environment). Tests inject fake agents for an offline run.
    """
    settings = get_settings()
    if agents is None:
        agents = build_agents(email_style)
    if include_inferred_contacts is None:
        include_inferred_contacts = settings.include_inferred_contacts
    max_workers = settings.max_workers

    # 1. Companies
    _emit(progress_cb, 1, "Finding companies...")
    companies = await _arun_company_finder(
        agents.company_finder, target_desc, offering_desc, max_companies=num_companies
    )
    _emit(progress_cb, 1, "Finding companies...", f"Found {len(companies)} companies")

    # 2 + 3. Contacts and research fanned out per company, run concurrently.
    _emit(progress_cb, 2, "Finding contacts + researching per company (parallel)...")
    if companies:
        contacts_data, research_data = await _fanout_contacts_and_research(
            agents, companies, target_desc, offering_desc, max_workers
        )
    else:
        contacts_data, research_data = [], []
    _emit(progress_cb, 2, "Finding contacts...", f"Collected contacts for {len(contacts_data)} companies")
    _emit(progress_cb, 3, "Researching insights...", f"Compiled research for {len(research_data)} companies")

    # 4. Emails — only for verified (non-inferred by default) contacts at
    #    companies that have non-empty research. No evidence -> no draft; those
    #    contacts stay visible for manual outreach instead.
    usable_contacts = _usable_contacts_for_emails(contacts_data, include_inferred_contacts)
    clean_research = _clean_research_for_emails(research_data)
    research_for_writer = [r for r in clean_research if r.get("insights")]
    emailable_contacts = _emailable_contacts(usable_contacts, research_for_writer)
    _emit(progress_cb, 4, "Writing personalized emails...")
    if emailable_contacts:
        emails = await _arun_email_writer(
            agents.email_writer,
            emailable_contacts,
            research_for_writer,
            offering_desc,
            sender_name or "Sales Team",
            sender_company or "Our Company",
            calendar_link or None,
        )
    else:
        emails = []
    _emit(
        progress_cb,
        4,
        "Writing personalized emails...",
        f"Generated {len(emails)} emails "
        f"({len(usable_contacts) - len(emailable_contacts)} companies skipped: no research)",
    )

    return {
        "companies": companies,
        "contacts": contacts_data,
        "research": research_data,
        "emails": emails,
    }


def run_pipeline(**kwargs: Any) -> Dict[str, Any]:
    """Synchronous wrapper around :func:`arun_pipeline` for sync callers (e.g.
    Streamlit). Async callers should ``await arun_pipeline(...)`` directly —
    calling this from inside a running event loop will raise, because
    ``asyncio.run`` cannot be nested.
    """
    return asyncio.run(arun_pipeline(**kwargs))
