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
    redis_url: str = "redis://localhost:6379/0"  # reserved for future multi-user caching (not wired)
    # Phase 6: separate application DB for campaign history (NOT the agno session DB).
    app_db_path: str = "tmp/campaigns.db"
    # Don't re-contact a company we generated outreach for within this many days.
    enable_contact_suppression: bool = True
    contact_cooldown_days: int = 30
    # Cooldown scope (multi-user): "user" = each account's own history; "global" =
    # across ALL accounts (cross-tenant dedup). There is no organization/team model,
    # so "global" is genuinely global, not org-scoped — set it knowingly.
    cooldown_scope: str = "user"

    # --- Web app / auth (multi-user FastAPI frontend) ---
    # Deployment environment. Defaults to "production" so a FORGOTTEN setting is
    # SECURE by default (a real deploy can't silently run with a weak secret and
    # insecure cookies). Local dev must OPT IN with ENVIRONMENT=development, which
    # relaxes the secret requirement and drops the cookie Secure flag for http.
    environment: str = "production"
    # Secret that signs session cookies (Starlette SessionMiddleware). MUST be
    # overridden outside development — see ``require_web_config``.
    session_secret: str = "dev-insecure-change-me"
    # Comma-separated public origins allowed to POST (CSRF guard). When set, the
    # origin check compares the browser Origin against THIS list instead of the
    # request host — correct behind a reverse proxy. Empty = fall back to host match.
    trusted_origins: str = ""
    # Hard ceiling on a single outreach run. A hung provider call is cancelled at
    # this deadline so it can't hold a concurrency slot forever.
    pipeline_timeout_seconds: int = 300

    @property
    def is_production(self) -> bool:
        return self.environment.strip().lower() != "development"

    def trusted_origin_list(self) -> List[str]:
        return [o.strip().rstrip("/") for o in self.trusted_origins.split(",") if o.strip()]

    def require_web_config(self) -> None:
        """Fail fast if the web app is misconfigured for production.

        Rejects both the known default secret AND any weak/short secret, so a
        deployment that copied a template placeholder can't silently run insecurely.
        """
        if self.is_production and (
            self.session_secret == "dev-insecure-change-me" or len(self.session_secret) < 32
        ):
            raise RuntimeError(
                "SESSION_SECRET must be a strong random value (>=32 chars) in "
                "production (ENVIRONMENT is not 'development'). Generate one with "
                "`python -c \"import secrets; print(secrets.token_urlsafe(32))\"`, "
                "or set ENVIRONMENT=development for local use."
            )

    # --- Model IDs (overridable per agent) ---
    company_finder_model: str = "gpt-5.4-nano"
    contact_finder_model: str = "gpt-4o"
    research_model: str = "gpt-5.4-nano"
    email_writer_model: str = "gpt-5.4-nano"

    # --- Concurrency / retries ---
    max_workers: int = 6
    api_max_retries: int = 3  # retry attempts for transient agent/API errors
    api_retry_wait: float = 0.5  # exponential backoff multiplier (seconds)
    cache_ttl_research: int = 604800  # 7 days
    cache_ttl_contacts: int = 259200  # 3 days

    # --- Observability ---
    log_level: str = "INFO"

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
