import pytest

from backend.config import Settings


def _bare_settings(**overrides) -> Settings:
    # _env_file=None ignores any local .env so the test is deterministic.
    return Settings(_env_file=None, **overrides)


def test_missing_keys_detected(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("EXA_API_KEY", raising=False)
    s = _bare_settings()
    assert s.missing_required_keys() == ["OPENAI_API_KEY", "EXA_API_KEY"]
    assert not s.has_openai_key and not s.has_exa_key


def test_require_keys_raises_when_missing(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("EXA_API_KEY", raising=False)
    s = _bare_settings()
    with pytest.raises(RuntimeError) as exc:
        s.require_keys()
    assert "OPENAI_API_KEY" in str(exc.value)


def test_keys_present_passes():
    s = _bare_settings(openai_api_key="x", exa_api_key="y")
    assert s.missing_required_keys() == []
    s.require_keys()  # should not raise


def test_require_openai_key_ignores_exa(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(RuntimeError) as exc:
        _bare_settings().require_openai_key()
    assert "OPENAI_API_KEY" in str(exc.value)
    # OpenAI present but Exa absent -> OK for OpenAI-only entrypoints.
    _bare_settings(openai_api_key="x").require_openai_key()


def test_default_model_ids():
    s = _bare_settings()
    assert s.company_finder_model == "gpt-5.4-nano"
    assert s.contact_finder_model == "gpt-4o"
