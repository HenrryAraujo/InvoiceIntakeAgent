"""
Application settings and the runtime model allow-list guard.

Settings are environment-driven (loaded from a local ``.env`` when present).
The model fields are validated against a strict allow-list so that any attempt
to use a non-permitted model fails fast at startup.
"""

from __future__ import annotations

from decimal import Decimal
from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from invoice_agent.domain.models import Persona

# Only these models may ever be used for agent, tool, judge, or validation calls.
ALLOWED_MODELS: frozenset[str] = frozenset({"gpt-5-mini", "gpt-5-nano"})
_LOG_LEVELS: frozenset[str] = frozenset({"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"})
_LOG_FORMATS: frozenset[str] = frozenset({"plain", "json"})
_REP_KEYS = frozenset({"rep", "representative", "customer_rep", "customer-representative"})
_SUPERVISOR_KEYS = frozenset({"supervisor", "sup", "supervisory"})


class Settings(BaseSettings):
    """Environment-driven configuration with a fail-fast model guard."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Secrets ---
    openai_api_key: str | None = Field(
        default=None,
        description="OpenAI API key. Required for live runs; optional for offline tests.",
    )

    # --- Input / output paths ---
    input_dir: Path = Field(default=Path("input_data"))
    output_dir: Path = Field(default=Path("output_data"))

    # --- Model selection (guarded by the allow-list) ---
    extractor_model: str = Field(default="gpt-5-mini")
    agent_model: str = Field(default="gpt-5-mini")
    judge_model: str = Field(default="gpt-5-nano")

    # --- Vision / cost controls ---
    render_dpi: int = Field(default=150, ge=72, le=300)
    max_pages: int = Field(default=4, ge=1, le=20)
    max_turns: int = Field(default=4, ge=2, le=12)

    # --- Logging (LOG_LEVEL: DEBUG=full local dev, INFO=minimal, WARNING=quiet) ---
    log_level: str = Field(default="DEBUG")
    log_format: str = Field(default="plain")

    # --- Observability ---
    enable_tracing: bool = Field(default=True)
    enable_judge: bool = Field(default=False)
    mlflow_tracking_uri: str = Field(default="sqlite:///mlflow.db")
    mlflow_experiment: str = Field(default="invoice-intake-agent")

    # --- HITL approval (persona-based Delegation of Authority) ---
    active_persona: str = Field(default="rep")
    approval_currency: str = Field(default="CAD")
    escalation_contact: str = Field(default="Finance Manager")
    hold_on_duplicate: bool = Field(default=False)
    persona_rep_title: str = Field(default="Customer Service Representative")
    persona_rep_limit: Decimal = Field(default=Decimal("10000"))
    persona_supervisor_title: str = Field(default="Customer Service Supervisor")
    persona_supervisor_limit: Decimal = Field(default=Decimal("150000"))

    @field_validator("extractor_model", "agent_model", "judge_model")
    @classmethod
    def _enforce_model_allow_list(cls, value: str) -> str:
        if value not in ALLOWED_MODELS:
            allowed = ", ".join(sorted(ALLOWED_MODELS))
            raise ValueError(
                f"Model '{value}' is not permitted. Allowed models: {allowed}."
            )
        return value

    @field_validator("log_level")
    @classmethod
    def _validate_log_level(cls, value: str) -> str:
        upper = value.strip().upper()
        if upper not in _LOG_LEVELS:
            raise ValueError(
                f"LOG_LEVEL '{value}' is invalid. Use one of: {', '.join(sorted(_LOG_LEVELS))}."
            )
        return upper

    @field_validator("log_format")
    @classmethod
    def _validate_log_format(cls, value: str) -> str:
        lower = value.strip().lower()
        if lower not in _LOG_FORMATS:
            raise ValueError(
                f"LOG_FORMAT '{value}' is invalid. Use one of: {', '.join(sorted(_LOG_FORMATS))}."
            )
        return lower

    @field_validator("active_persona")
    @classmethod
    def _validate_active_persona(cls, value: str) -> str:
        norm = value.strip().lower()
        if norm not in (_REP_KEYS | _SUPERVISOR_KEYS):
            raise ValueError(
                f"ACTIVE_PERSONA '{value}' is invalid. Use 'rep' or 'supervisor'."
            )
        return norm

    def resolve_persona(self, key: str | None = None) -> Persona:
        """Resolve the acting persona (defaults to ``active_persona``) to a domain ``Persona``."""
        chosen = (key or self.active_persona or "rep").strip().lower()
        if chosen in _REP_KEYS:
            return Persona(
                key="rep",
                title=self.persona_rep_title,
                approval_limit=self.persona_rep_limit,
                currency=self.approval_currency,
            )
        if chosen in _SUPERVISOR_KEYS:
            return Persona(
                key="supervisor",
                title=self.persona_supervisor_title,
                approval_limit=self.persona_supervisor_limit,
                currency=self.approval_currency,
            )
        raise ValueError(f"Unknown persona '{key}'. Use 'rep' or 'supervisor'.")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a cached, validated ``Settings`` instance (fail-fast on bad config)."""
    return Settings()
