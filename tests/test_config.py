"""Settings tests: path defaults, logging + persona validation, persona resolution."""

from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from invoice_agent.config import Settings


def _settings(**overrides) -> Settings:
    # _env_file=None bypasses the local .env so we exercise pure field defaults.
    params = {"openai_api_key": "test-key", "_env_file": None}
    params.update(overrides)
    return Settings(**params)


def test_path_defaults_are_input_and_output_data():
    settings = _settings()
    assert settings.input_dir == Path("input_data")
    assert settings.output_dir == Path("output_data")


def test_log_level_defaults_to_debug_and_normalizes_case():
    assert _settings().log_level == "DEBUG"
    assert _settings(log_level="info").log_level == "INFO"


def test_invalid_log_level_rejected():
    with pytest.raises(ValidationError):
        _settings(log_level="verbose")


def test_invalid_log_format_rejected():
    with pytest.raises(ValidationError):
        _settings(log_format="xml")


def test_invalid_active_persona_rejected():
    with pytest.raises(ValidationError):
        _settings(active_persona="ceo")


def test_resolve_persona_rep_and_supervisor():
    settings = _settings(
        persona_rep_limit=Decimal("10000"),
        persona_supervisor_limit=Decimal("150000"),
    )
    rep = settings.resolve_persona("rep")
    sup = settings.resolve_persona("supervisor")
    assert rep.key == "rep" and rep.approval_limit == Decimal("10000")
    assert sup.key == "supervisor" and sup.approval_limit == Decimal("150000")
    assert rep.currency == settings.approval_currency


def test_resolve_persona_defaults_to_active_persona():
    assert _settings(active_persona="supervisor").resolve_persona().key == "supervisor"


def test_resolve_persona_unknown_raises():
    with pytest.raises(ValueError):
        _settings().resolve_persona("manager")
