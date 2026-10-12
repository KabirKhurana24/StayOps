"""Application settings.

All configuration comes from environment variables, optionally loaded from the
repo-root `.env` (shared with apps/web) and then `apps/api/.env` for overrides.
Nothing here has a production secret as a default: the API boots without
Supabase credentials, but authenticated endpoints will reject every request
until a verification key is configured.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

API_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = API_ROOT.parents[1]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(REPO_ROOT / ".env", API_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "StayOps API"
    app_version: str = "0.1.0"
    app_env: Literal["development", "test", "staging", "production"] = "development"
    log_level: str = "INFO"
    log_json: bool | None = None  # default: JSON outside development

    database_url: str = "postgresql+psycopg://postgres:postgres@localhost:5432/stayops"
    test_database_url: str | None = None
    db_pool_size: int = 5
    db_max_overflow: int = 5

    supabase_url: str | None = None
    supabase_publishable_key: SecretStr | None = None
    supabase_secret_key: SecretStr | None = None
    supabase_jwt_secret: SecretStr | None = None
    supabase_jwt_audience: str = "authenticated"

    # NoDecode: accept a plain comma-separated string instead of requiring JSON.
    api_cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:3000"]
    )

    redis_url: str | None = None

    # --- Operations (Phase 3) -------------------------------------------------
    # A same-day turnover shorter than this is an operational risk (Command Center
    # signal, HIGH-priority cleaning). See docs/operations.md.
    tight_turnover_minutes: int = Field(default=180, ge=15, le=24 * 60)
    # When true a completed cleaning waits for inspection before the unit is ready.
    cleaning_requires_inspection: bool = True

    # --- Event worker (outbox processing) -------------------------------------
    # Run the outbox worker as a background thread inside the API process. Defaults
    # to on in development so `pnpm dev:api` is all you need; production runs
    # `python -m app.events.worker` as its own process instead.
    event_worker_embedded: bool | None = None
    event_worker_poll_seconds: float = Field(default=2.0, gt=0, le=60)
    event_worker_batch_size: int = Field(default=50, ge=1, le=500)
    # Attempts before an event is parked as FAILED with no further retries.
    event_max_attempts: int = Field(default=8, ge=1, le=50)
    # A PROCESSING claim older than this is considered abandoned (worker crashed).
    event_lease_seconds: int = Field(default=300, ge=10, le=3600)

    # --- AI (Phase 5) ------------------------------------------------------------
    # disabled: no agent runs (default — nothing answers guests unless configured).
    # mock:     deterministic in-process provider (tests, evals, demos; no network).
    # openai:   OpenAI Chat Completions with tool calling (needs OPENAI_API_KEY).
    ai_provider: Literal["disabled", "mock", "openai"] = "disabled"
    openai_api_key: SecretStr | None = None
    openai_model: str = "gpt-4.1-mini"
    openai_base_url: str = "https://api.openai.com/v1"
    ai_timeout_seconds: float = Field(default=30.0, gt=0, le=120)
    ai_max_tool_rounds: int = Field(default=4, ge=1, le=10)
    # USD per million tokens, for cost estimates (provider prices change; set yours).
    ai_input_cost_per_mtok: float = Field(default=0.40, ge=0)
    ai_output_cost_per_mtok: float = Field(default=1.60, ge=0)

    # --- Autopilot (Phase 6) ------------------------------------------------------
    # Scheduled triggers are evaluated by the worker at most this often.
    automation_schedule_interval_seconds: int = Field(default=60, ge=5, le=3600)
    # Executions claimed per worker pass, and automatic retries of unexpected errors
    # (business failures are not retried automatically; operators retry them).
    automation_batch_size: int = Field(default=20, ge=1, le=200)
    automation_max_auto_attempts: int = Field(default=3, ge=1, le=10)
    automation_max_manual_retries: int = Field(default=5, ge=1, le=20)

    # Seed-only values.
    seed_demo_email: str = "demo@stayops.dev"
    seed_demo_password: SecretStr | None = None

    @field_validator("api_cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        if isinstance(value, str):
            stripped = value.strip()
            if stripped.startswith("["):
                return json.loads(stripped)
            return [origin.strip() for origin in stripped.split(",") if origin.strip()]
        return value

    @field_validator("api_cors_origins")
    @classmethod
    def _reject_wildcards(cls, value: list[str]) -> list[str]:
        if any("*" in origin for origin in value):
            raise ValueError("API_CORS_ORIGINS must list exact origins; wildcards are not allowed")
        return value

    @field_validator("supabase_url", "redis_url", "test_database_url", mode="before")
    @classmethod
    def _empty_to_none(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator(
        "supabase_publishable_key",
        "supabase_secret_key",
        "supabase_jwt_secret",
        "seed_demo_password",
        "openai_api_key",
        mode="before",
    )
    @classmethod
    def _empty_secret_to_none(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @property
    def is_development(self) -> bool:
        return self.app_env == "development"

    @property
    def use_json_logs(self) -> bool:
        return self.log_json if self.log_json is not None else not self.is_development

    @property
    def ai_enabled(self) -> bool:
        if self.ai_provider == "openai":
            return self.openai_api_key is not None
        return self.ai_provider == "mock"

    @property
    def run_embedded_worker(self) -> bool:
        if self.event_worker_embedded is not None:
            return self.event_worker_embedded
        return self.is_development

    @property
    def supabase_issuer(self) -> str | None:
        return f"{self.supabase_url.rstrip('/')}/auth/v1" if self.supabase_url else None

    @property
    def supabase_jwks_url(self) -> str | None:
        issuer = self.supabase_issuer
        return f"{issuer}/.well-known/jwks.json" if issuer else None


@lru_cache
def get_settings() -> Settings:
    return Settings()
