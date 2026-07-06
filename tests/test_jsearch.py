from backend.models import Insight
from backend.sources.jsearch import (
    _condense_job,
    _employer_matches,
    _extract_tech,
    _normalize_company_name,
    fetch_job_insights,
)


def _job(**overrides):
    base = {
        "job_id": "id-1",
        "job_title": "Senior Backend Engineer",
        "employer_name": "IBM",
        "employer_website": None,  # frequently null in real data
        "job_apply_link": "https://example.com/apply/1",
        "job_description": "We use Python, Go and Kubernetes on AWS.",
        "job_posted_at": "2 days ago",
        "job_city": "Chicago",
        "job_state": "Illinois",
        "job_is_remote": False,
        "job_publisher": "LinkedIn",
    }
    base.update(overrides)
    return base


def _raw(jobs):
    def _fetch_raw(query, country, settings):
        return {"status": "OK", "data": jobs}

    return _fetch_raw


# --- pure helpers ---

def test_normalize_strips_legal_suffixes_and_punctuation():
    assert _normalize_company_name("Acme, Inc.") == "acme"
    assert _normalize_company_name("Globex LLC") == "globex"


def test_employer_matches_ignores_suffixes():
    assert _employer_matches("IBM Corp", "IBM")
    assert _employer_matches("Acme Inc", "acme")
    assert not _employer_matches("Jobot", "IBM")  # staffing repost


def test_extract_tech_word_boundaries():
    tech = _extract_tech("We use Python and Go, plus Kubernetes.")
    assert "python" in tech and "go" in tech and "kubernetes" in tech
    # 'go' must not spuriously match inside 'good governance'
    assert "go" not in _extract_tech("good governance and great growth")


def test_condense_job_includes_title_location_signal():
    text = _condense_job(_job())
    assert "Senior Backend Engineer" in text
    assert "Chicago, Illinois" in text
    assert "2 days ago" in text
    assert "python" in text.lower()


def test_condense_job_handles_remote_and_missing_fields():
    text = _condense_job(_job(job_is_remote=True, job_city=None, job_state=None, job_description=None))
    assert "Remote" in text


# --- fetch_job_insights behavior ---

def test_fetch_filters_to_matching_employer():
    jobs = [
        _job(job_id="a", employer_name="IBM"),
        _job(job_id="b", employer_name="Jobot"),          # staffing repost -> filtered
        _job(job_id="c", employer_name="IBM Corporation"),
    ]
    out = fetch_job_insights("IBM", fetch_raw=_raw(jobs))
    assert all(isinstance(i, Insight) for i in out)
    assert len(out) == 2  # b excluded


def test_fetch_dedups_by_job_id():
    jobs = [_job(job_id="dup"), _job(job_id="dup"), _job(job_id="other")]
    out = fetch_job_insights("IBM", fetch_raw=_raw(jobs))
    assert len(out) == 2


def test_fetch_respects_max_jobs():
    jobs = [_job(job_id=str(i)) for i in range(10)]
    out = fetch_job_insights("IBM", fetch_raw=_raw(jobs), max_jobs=3)
    assert len(out) == 3


def test_fetch_returns_job_posting_source_type():
    out = fetch_job_insights("IBM", fetch_raw=_raw([_job()]))
    assert out[0].source_type == "job_posting"
    assert out[0].source_url == "https://example.com/apply/1"


def test_fetch_graceful_on_missing_key(monkeypatch):
    # No injected fetch_raw and no key -> silent [] (no network).
    from backend.config import Settings

    out = fetch_job_insights("IBM", settings=Settings(_env_file=None))
    assert out == []


def test_fetch_graceful_on_exception():
    def boom(query, country, settings):
        raise RuntimeError("network down")

    assert fetch_job_insights("IBM", fetch_raw=boom) == []


def test_fetch_graceful_on_malformed_json():
    assert fetch_job_insights("IBM", fetch_raw=lambda q, c, s: "not a dict") == []
    assert fetch_job_insights("IBM", fetch_raw=lambda q, c, s: {"data": None}) == []
