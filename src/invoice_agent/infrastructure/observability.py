"""
MLflow observability — privacy-safe, deterministic per-run telemetry.

Only metrics, safe params, and content **hashes** are recorded. No raw email/PDF text,
prompts, responses, or rendered images are ever logged (AC7). MLflow autologging is **not**
enabled, so nothing is captured implicitly. All MLflow calls are best-effort: a tracing
failure never breaks a request.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Optional

from invoice_agent.config import Settings
from invoice_agent.domain.ports import RunTracer

logger = logging.getLogger(__name__)

# Approximate USD prices per 1M tokens (input, output). Used only for the deterministic
# ``estimated_cost_usd`` metric — not for billing. Adjust as pricing changes.
_PRICING_PER_MTOK: dict[str, tuple[float, float]] = {
    "gpt-5-mini": (0.25, 2.00),
    "gpt-5-nano": (0.05, 0.40),
}


@dataclass(frozen=True)
class TokenUsage:
    """Token usage from a single model call or an aggregate of several."""

    input_tokens: int = 0
    output_tokens: int = 0
    requests: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def __add__(self, other: "TokenUsage") -> "TokenUsage":
        return TokenUsage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            requests=self.requests + other.requests,
        )


def estimate_cost(usage: TokenUsage, model: str) -> float:
    """Approximate USD cost for the given usage + model (0.0 for unknown models)."""
    prices = _PRICING_PER_MTOK.get(model)
    if prices is None:
        return 0.0
    in_price, out_price = prices
    return (usage.input_tokens / 1_000_000.0) * in_price + (
        usage.output_tokens / 1_000_000.0
    ) * out_price


class _NullRunHandle:
    def log_params(self, params: Mapping[str, object]) -> None: ...

    def log_metrics(self, metrics: Mapping[str, float]) -> None: ...

    def set_tags(self, tags: Mapping[str, str]) -> None: ...

    def __enter__(self) -> "_NullRunHandle":
        return self

    def __exit__(self, *exc_info: object) -> None:
        return None


class NullRunTracer:
    """No-op tracer (used when tracing is disabled or in offline tests)."""

    def start_run(self, name: str) -> _NullRunHandle:
        return _NullRunHandle()


class _MlflowRunHandle:
    def __init__(self, mlflow_module: object) -> None:
        self._mlflow = mlflow_module

    def log_params(self, params: Mapping[str, object]) -> None:
        try:
            self._mlflow.log_params(dict(params))  # type: ignore[attr-defined]
        except Exception:
            logger.debug("log_params failed", exc_info=True)

    def log_metrics(self, metrics: Mapping[str, float]) -> None:
        try:
            self._mlflow.log_metrics({k: float(v) for k, v in metrics.items()})  # type: ignore[attr-defined]
        except Exception:
            logger.debug("log_metrics failed", exc_info=True)

    def set_tags(self, tags: Mapping[str, str]) -> None:
        try:
            self._mlflow.set_tags(dict(tags))  # type: ignore[attr-defined]
        except Exception:
            logger.debug("set_tags failed", exc_info=True)

    def __enter__(self) -> "_MlflowRunHandle":
        return self

    def __exit__(self, *exc_info: object) -> None:
        try:
            self._mlflow.end_run()  # type: ignore[attr-defined]
        except Exception:
            logger.debug("end_run failed", exc_info=True)


class MlflowRunTracer:
    """Logs a privacy-safe MLflow run per request (MLflow is configured lazily)."""

    def __init__(self, tracking_uri: str, experiment: str) -> None:
        self._tracking_uri = tracking_uri
        self._experiment = experiment
        self._configured = False

    def _configure(self) -> object:
        import mlflow  # local import keeps module load light and side-effect free

        if not self._configured:
            mlflow.set_tracking_uri(self._tracking_uri)
            mlflow.set_experiment(self._experiment)
            self._configured = True
        return mlflow

    def start_run(self, name: str) -> object:
        try:
            mlflow = self._configure()
            mlflow.start_run(run_name=name)  # type: ignore[attr-defined]
            return _MlflowRunHandle(mlflow)
        except Exception:
            logger.debug("could not start MLflow run; tracing skipped for this call", exc_info=True)
            return _NullRunHandle()


def make_tracer(settings: Settings) -> RunTracer:
    """Return an MLflow tracer when enabled, otherwise a no-op tracer."""
    if not settings.enable_tracing:
        return NullRunTracer()
    try:
        return MlflowRunTracer(settings.mlflow_tracking_uri, settings.mlflow_experiment)
    except Exception:
        logger.debug("falling back to NullRunTracer", exc_info=True)
        return NullRunTracer()


def judge_faithfulness(summary: str, payload_json: str, settings: Settings) -> Optional[float]:
    """Optional ``gpt-5-nano`` LLM-as-judge faithfulness score in [0, 1]. Off by default."""
    try:
        from openai import OpenAI

        client = OpenAI(api_key=settings.openai_api_key)
        response = client.responses.create(
            model=settings.judge_model,
            instructions=(
                "You are a strict evaluator. Given an invoice JSON payload and a Customer "
                "Service summary, rate how faithfully and completely the summary reflects the "
                'payload. Return ONLY a JSON object: {"score": <number between 0 and 1>}.'
            ),
            input=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": f"PAYLOAD:\n{payload_json}\n\nSUMMARY:\n{summary}",
                        }
                    ],
                }
            ],
            reasoning={"effort": "low"},
            text={"format": {"type": "json_object"}, "verbosity": "low"},
            max_output_tokens=2000,
            store=False,
        )
        score = float(json.loads(response.output_text).get("score"))
        return max(0.0, min(1.0, score))
    except Exception:
        logger.debug("judge failed (non-fatal)", exc_info=True)
        return None
