"""Typed application configuration, loaded once from the environment / ``.env``.

Keys come only from the environment (or a local ``.env``) — never from the UI.
``Settings`` is instantiated lazily via ``get_settings()`` so importing this
module never reads the environment or fails on missing keys; that keeps the
offline test harness import-clean. Fail-fast on missing keys happens at the
point real agents are built (``require_keys``), not at import.
"""

import os
from functools import lru_cache
from typing import List, Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Field names map case-insensitively to env vars (openai_api_key <- OPENAI_API_KEY).
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # --- Required provider keys ---
    openai_api_key: str = ""
    exa_api_key: str = ""

    # --- Forward-looking (later phases); optional with sane defaults ---
    rapidapi_key: str = ""  # Phase 4: job postings (JSearch)
    jsearch_country: str = "us"
    jsearch_max_jobs: int = 5
    jsearch_max_concurrency: int = 2  # own bound so slow JSearch can't throttle LLM work
    redis_url: str = "redis://localhost:6379/0"  # Phase 8: cache
    # Phase 6: separate application DB for campaign history (NOT the agno session DB).
    app_db_path: str = "tmp/campaigns.db"
    # Don't re-contact a company we generated outreach for within this many days.
    enable_contact_suppression: bool = True
    contact_cooldown_days: int = 30

    # --- Model IDs (overridable per agent) ---
    company_finder_model: str = "gpt-5.4-nano"
    contact_finder_model: str = "gpt-4o"
    research_model: str = "gpt-5.4-nano"
    email_writer_model: str = "gpt-5.4-nano"

    # --- Concurrency / cache tuning (used in later phases) ---
    max_workers: int = 6
    cache_ttl_research: int = 604800  # 7 days
    cache_ttl_contacts: int = 259200  # 3 days

    # --- Behavior / safety ---
    # Agno agent debug logging can print prompts/responses (company targets,
    # contact emails, research). Off by default; enable only in local dev.
    debug_agents: bool = False
    # Inferred (guessed) contact emails carry deliverability/privacy/compliance
    # risk. Excluded from generated outreach by default; opt in explicitly.
    include_inferred_contacts: bool = False

    # --- Email evaluation (Phase 4b deterministic gate) ---
    email_min_words: int = 40
    email_max_words: int = 200

    # --- LLM faithfulness judge (Phase 4c) ---
    # Paid; runs only on emails that pass the deterministic gate. Off by default.
    enable_llm_judge: bool = False
    judge_model: str = "gpt-4o"  # stronger model for judgment; few calls (gated)
    judge_max_concurrency: int = 2  # own bound to smooth rate-limit/cost spikes
    # One bounded regeneration when the judge finds an email unfaithful. Only runs
    # when the judge runs; capped at a single retry to avoid a cost spiral.
    enable_repair: bool = True

    @property
    def has_openai_key(self) -> bool:
        return bool(self.openai_api_key)

    @property
    def has_exa_key(self) -> bool:
        return bool(self.exa_api_key)

    def missing_required_keys(self) -> List[str]:
        missing = []
        if not self.openai_api_key:
            missing.append("OPENAI_API_KEY")
        if not self.exa_api_key:
            missing.append("EXA_API_KEY")
        return missing

    def require_keys(self) -> None:
        """Raise a clear error if a required provider key is absent."""
        missing = self.missing_required_keys()
        if missing:
            raise RuntimeError(
                "Missing required API key(s): "
                + ", ".join(missing)
                + ". Set them in your .env file (see .env.example)."
            )

    def require_openai_key(self) -> None:
        """Raise if OpenAI is unavailable. For OpenAI-only entrypoints (e.g. the
        golden-set judge eval) that don't need Exa."""
        if not self.openai_api_key:
            raise RuntimeError(
                "Missing required API key: OPENAI_API_KEY. "
                "Set it in your .env file (see .env.example)."
            )


@lru_cache
def get_settings() -> Settings:
    return Settings()


def export_provider_env(settings: Optional[Settings] = None) -> None:
    """Push keys into ``os.environ`` so the agno/openai/exa SDKs discover them.

    Those libraries read ``OPENAI_API_KEY`` / ``EXA_API_KEY`` directly from the
    process environment, so config values loaded from ``.env`` must be exported.
    """
    settings = settings or get_settings()
    if settings.openai_api_key:
        os.environ.setdefault("OPENAI_API_KEY", settings.openai_api_key)
    if settings.exa_api_key:
        os.environ.setdefault("EXA_API_KEY", settings.exa_api_key)
