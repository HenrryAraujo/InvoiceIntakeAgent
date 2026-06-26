"""Application settings and the runtime model allow-list guard.

Settings are environment-driven (loaded from a local ``.env`` when present).
The model fields are validated against a strict allow-list so that any attempt
to use a non-permitted model fails fast at startup.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Only these models may ever be used for agent, tool, judge, or validation calls.
ALLOWED_MODELS: frozenset[str] = frozenset({"gpt-5-mini", "gpt-5-nano"})


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
    input_dir: Path = Field(default=Path("data"))
    output_dir: Path = Field(default=Path("output"))

    # --- Model selection (guarded by the allow-list) ---
    extractor_model: str = Field(default="gpt-5-mini")
    agent_model: str = Field(default="gpt-5-mini")
    judge_model: str = Field(default="gpt-5-nano")

    # --- Vision / cost controls ---
    render_dpi: int = Field(default=150, ge=72, le=300)
    max_pages: int = Field(default=4, ge=1, le=20)
    max_turns: int = Field(default=4, ge=2, le=12)

    # --- Observability ---
    enable_tracing: bool = Field(default=True)
    enable_judge: bool = Field(default=False)
    mlflow_tracking_uri: str = Field(default="sqlite:///mlflow.db")
    mlflow_experiment: str = Field(default="invoice-intake-agent")

    @field_validator("extractor_model", "agent_model", "judge_model")
    @classmethod
    def _enforce_model_allow_list(cls, value: str) -> str:
        if value not in ALLOWED_MODELS:
            allowed = ", ".join(sorted(ALLOWED_MODELS))
            raise ValueError(
                f"Model '{value}' is not permitted. Allowed models: {allowed}."
            )
        return value


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a cached, validated ``Settings`` instance (fail-fast on bad config)."""
    return Settings()
