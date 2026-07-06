"""JSearch (RapidAPI) job-postings evidence source.

Open roles are deterministic, verifiable sales signals (scaling pain, tech stack,
new functions), fetched as raw structured postings — no LLM summarization layer
to hallucinate through, which makes them the cleanest input for the faithfulness
eval later in Phase 4.

Design notes baked in from the real API payload:
- ``employer_website`` is frequently ``null``, so company matching is done on the
  normalized ``employer_name`` — NEVER ``job_publisher`` (that's LinkedIn/Google).
- ``date_posted=month`` + ``num_pages=1`` keep the signal fresh and quota low.
- Graceful degradation: missing key / HTTP error / bad JSON all yield ``[]``,
  never an exception — a company simply gets fewer evidence units.
- ``httpx`` + ``tenacity`` are imported lazily so importing this module (and the
  pipeline) requires neither; only the real network path pulls them in.
"""

import re
from typing import Any, Callable, Dict, List, Optional

from backend.config import Settings, get_settings
from backend.models import Insight

_HOST = "jsearch.p.rapidapi.com"
_LEGAL_SUFFIXES = {"inc", "llc", "ltd", "corp", "co", "company", "gmbh", "plc", "sa", "ag"}

# Curated tech/skill vocabulary for pulling signal out of a ~600-word JD wall.
_TECH_TERMS = [
    "python", "java", "javascript", "typescript", "golang", "go", "rust", "c++", "c#",
    "ruby", "php", "scala", "kotlin", "swift",
    "react", "angular", "vue", "node", "django", "flask", "spring", ".net", "rails",
    "aws", "gcp", "azure", "kubernetes", "docker", "terraform",
    "postgres", "postgresql", "mysql", "mongodb", "redis", "kafka", "snowflake",
    "spark", "hadoop", "airflow", "dbt",
    "salesforce", "hubspot", "sap",
    "machine learning", "llm", "genai", "generative ai", "nlp", "pytorch", "tensorflow",
]


def _normalize_company_name(name: str) -> str:
    s = re.sub(r"[^a-z0-9 ]", " ", (name or "").lower())
    tokens = [t for t in s.split() if t and t not in _LEGAL_SUFFIXES]
    return " ".join(tokens).strip()


def _employer_matches(employer_name: Optional[str], target_name: str) -> bool:
    emp = _normalize_company_name(employer_name or "")
    tgt = _normalize_company_name(target_name)
    if not emp or not tgt:
        return False
    return emp == tgt or tgt in emp or emp in tgt


def _extract_tech(description: str, limit: int = 6) -> List[str]:
    text = (description or "").lower()
    found: List[str] = []
    for term in _TECH_TERMS:
        # Boundaries so 'go' doesn't match 'good' and 'react' not 'reactor'.
        if re.search(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", text):
            found.append(term)
        if len(found) >= limit:
            break
    return found


def _location(job: Dict[str, Any]) -> str:
    if job.get("job_is_remote"):
        return "Remote"
    parts = [job.get("job_city"), job.get("job_state") or job.get("job_country")]
    return ", ".join([p for p in parts if p]) or "location n/a"


def _condense_job(job: Dict[str, Any]) -> str:
    title = job.get("job_title") or "Open role"
    posted = job.get("job_posted_at") or "recently"
    tech = _extract_tech(job.get("job_description") or "")
    signal = f" Signals: {', '.join(tech)}." if tech else ""
    return f"Hiring: {title} ({_location(job)}, posted {posted}).{signal}"


def _to_insight(job: Dict[str, Any]) -> Insight:
    return Insight(
        text=_condense_job(job),
        source_url=job.get("job_apply_link"),
        source_type="job_posting",
    )


def _default_fetch_raw(query: str, country: str, settings: Settings) -> Dict[str, Any]:
    import httpx  # lazy
    from tenacity import (
        retry,
        retry_if_exception_type,
        stop_after_attempt,
        wait_exponential,
    )

    headers = {"x-rapidapi-key": settings.rapidapi_key, "x-rapidapi-host": _HOST}
    params = {"query": query, "num_pages": "1", "country": country, "date_posted": "month"}

    @retry(
        reraise=True,
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=0.5, max=4),
        retry=retry_if_exception_type(httpx.HTTPError),
    )
    def _do() -> Dict[str, Any]:
        resp = httpx.get(f"https://{_HOST}/search", params=params, headers=headers, timeout=15.0)
        resp.raise_for_status()
        return resp.json()

    return _do()


def fetch_job_insights(
    company_name: str,
    *,
    country: Optional[str] = None,
    max_jobs: Optional[int] = None,
    fetch_raw: Optional[Callable[[str, str, Settings], Dict[str, Any]]] = None,
    settings: Optional[Settings] = None,
) -> List[Insight]:
    """Return condensed job-posting evidence for ``company_name`` (best-effort).

    Never raises: no key, network failure, or malformed JSON all yield ``[]``.
    ``fetch_raw`` is injectable so tests exercise filtering/condensation offline.
    """
    settings = settings or get_settings()
    if fetch_raw is None and not settings.rapidapi_key:
        return []  # no key configured -> skip silently
    country = country or settings.jsearch_country
    max_jobs = max_jobs or settings.jsearch_max_jobs
    fetch_raw = fetch_raw or _default_fetch_raw

    try:
        data = fetch_raw(f"jobs at {company_name}", country, settings)
    except Exception:
        return []
    if not isinstance(data, dict):
        return []

    insights: List[Insight] = []
    seen_ids = set()
    for job in data.get("data") or []:
        if not isinstance(job, dict):
            continue
        if not _employer_matches(job.get("employer_name"), company_name):
            continue  # reject name collisions / staffing reposts
        jid = job.get("job_id")
        if jid is not None and jid in seen_ids:
            continue
        seen_ids.add(jid)
        insights.append(_to_insight(job))
        if len(insights) >= max_jobs:
            break
    return insights
