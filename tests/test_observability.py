"""Observability tests: token usage, cost estimate, tracer selection (no MLflow writes)."""

from invoice_agent.config import Settings
from invoice_agent.infrastructure.observability import (
    MlflowRunTracer,
    NullRunTracer,
    TokenUsage,
    estimate_cost,
    make_tracer,
)


def test_token_usage_add_and_total():
    total = TokenUsage(input_tokens=10, output_tokens=20, requests=1) + TokenUsage(
        input_tokens=5, output_tokens=0, requests=1
    )
    assert total.input_tokens == 15
    assert total.output_tokens == 20
    assert total.requests == 2
    assert total.total_tokens == 35


def test_estimate_cost_known_and_unknown_models():
    usage = TokenUsage(input_tokens=1_000_000, output_tokens=1_000_000)
    assert estimate_cost(usage, "gpt-5-mini") == 0.25 + 2.00
    assert estimate_cost(usage, "gpt-5-nano") == 0.05 + 0.40
    assert estimate_cost(usage, "gpt-4o") == 0.0


def test_make_tracer_disabled_returns_null():
    assert isinstance(make_tracer(Settings(openai_api_key="x", enable_tracing=False)), NullRunTracer)


def test_make_tracer_enabled_returns_mlflow():
    assert isinstance(make_tracer(Settings(openai_api_key="x", enable_tracing=True)), MlflowRunTracer)


def test_null_tracer_handle_is_noop():
    with NullRunTracer().start_run("x") as run:
        run.log_params({"a": 1})
        run.log_metrics({"m": 1.0})
        run.set_tags({"t": "v"})
