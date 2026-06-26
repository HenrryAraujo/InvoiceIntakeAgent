"""OpenAI Agents SDK runner with exactly two tools.

The agent orchestrates the required tool sequence: ``extract_invoice_data`` then
``send_notification``. Tool dependencies (extractor, notifier, the loaded email and the
resolved PDF path) are passed via the run *context*, so the tools take no large arguments
and the LLM only composes the human-readable summary.
"""

from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

from agents import (
    Agent,
    ModelSettings,
    RunContextWrapper,
    Runner,
    function_tool,
    set_default_openai_key,
    set_tracing_disabled,
)
from openai.types.shared import Reasoning

from invoice_agent.config import Settings
from invoice_agent.domain.models import InboundEmail, InvoiceData, OutboundNotification
from invoice_agent.domain.ports import InvoiceExtractor, NotificationSender, RunTracer
from invoice_agent.infrastructure.notifier import render_summary
from invoice_agent.infrastructure.observability import (
    NullRunTracer,
    TokenUsage,
    estimate_cost,
    judge_faithfulness,
)

logger = logging.getLogger(__name__)


class AgentRunError(Exception):
    """Raised when the agent run cannot produce a notification."""


@dataclass
class _ToolContext:
    settings: Settings
    email: InboundEmail
    pdf_path: Path
    extractor: InvoiceExtractor
    notifier: NotificationSender
    extracted: InvoiceData | None = None
    notification: OutboundNotification | None = None
    tool_calls: list[str] = field(default_factory=list)
    timings: dict[str, float] = field(default_factory=dict)


@function_tool
def extract_invoice_data(ctx: RunContextWrapper[_ToolContext]) -> str:
    """Extract structured invoice fields from the attached PDF, including fields that appear
    only inside embedded images (such as the invoice number). Returns the extracted invoice
    data as a JSON string. Call this exactly once, before send_notification."""
    context = ctx.context
    context.tool_calls.append("extract_invoice_data")
    started = time.perf_counter()
    data = context.extractor.extract(context.pdf_path, context.email)
    context.timings["extract_invoice_data"] = (time.perf_counter() - started) * 1000.0
    context.extracted = data
    return data.model_dump_json()


@function_tool
def send_notification(ctx: RunContextWrapper[_ToolContext], summary: str) -> str:
    """Send the Customer Service notification. `summary` must be a clear, human-readable,
    sectioned/bulleted summary of the invoice for Customer Service. Writes the summary and the
    structured JSON payload to the output files and returns a confirmation. Call this exactly
    once, after extract_invoice_data."""
    context = ctx.context
    context.tool_calls.append("send_notification")
    if context.extracted is None:
        return "ERROR: extract_invoice_data has not been called yet; call it first."
    started = time.perf_counter()
    text = (summary or "").strip() or render_summary(context.extracted)
    notification = OutboundNotification(summary=text, payload=context.extracted)
    confirmation = context.notifier.send(notification)
    context.timings["send_notification"] = (time.perf_counter() - started) * 1000.0
    context.notification = notification
    return confirmation


_INSTRUCTIONS = (
    "You are an invoice-intake agent for an Accounts Payable team. Process the inbound vendor "
    "invoice using your two tools, performing each step exactly once and in order:\n"
    "1) Call extract_invoice_data to obtain structured invoice fields (including image-only "
    "fields such as the invoice number).\n"
    "2) Compose a concise, well-structured Customer Service summary covering vendor, invoice "
    "number, customer PO, invoice/due dates and payment terms, currency, subtotal, tax breakdown "
    "and total due, line items, ship-to/site allocations, and important notes (delivery windows, "
    "receiving requirements, duplicate warnings). Then call send_notification with that summary.\n"
    "Do not call any tool more than once. Do not fabricate data. Do not paste raw PDF text."
)


def _agent_usage(result: object) -> TokenUsage:
    try:
        usage = result.context_wrapper.usage  # type: ignore[attr-defined]
        return TokenUsage(
            input_tokens=int(getattr(usage, "input_tokens", 0) or 0),
            output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
            requests=int(getattr(usage, "requests", 0) or 0),
        )
    except Exception:
        return TokenUsage()


def _sha256_text(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8", "ignore")).hexdigest()[:16]


def _sha256_file(path: Path) -> str:
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()[:16]
    except Exception:
        return ""


class AgentInvoiceRunner:
    """``InvoiceAgentRunner`` adapter built on the OpenAI Agents SDK."""

    def __init__(
        self,
        settings: Settings,
        extractor: InvoiceExtractor,
        notifier: NotificationSender,
        tracer: RunTracer | None = None,
    ) -> None:
        self._settings = settings
        self._extractor = extractor
        self._notifier = notifier
        self._tracer: RunTracer = tracer if tracer is not None else NullRunTracer()
        self.last_tool_sequence: list[str] = []

        if settings.openai_api_key:
            set_default_openai_key(settings.openai_api_key, use_for_tracing=False)
        # SDK-native tracing is disabled here; MLflow telemetry is emitted via self._tracer.
        set_tracing_disabled(True)

        self._agent: Agent[_ToolContext] = Agent(
            name="InvoiceIntakeAgent",
            instructions=_INSTRUCTIONS,
            model=settings.agent_model,
            model_settings=ModelSettings(reasoning=Reasoning(effort="low"), verbosity="low"),
            tools=[extract_invoice_data, send_notification],
        )

    def run(self, email: InboundEmail, pdf_path: Path) -> OutboundNotification:
        context = _ToolContext(
            settings=self._settings,
            email=email,
            pdf_path=pdf_path,
            extractor=self._extractor,
            notifier=self._notifier,
        )
        prompt = (
            "Process this inbound vendor invoice email and notify Customer Service.\n"
            f"Subject: {email.subject or '(no subject)'}\n"
            f"From: {email.from_ or '(unknown)'}\n"
            "The PDF attachment is available to your tools. Call extract_invoice_data, then "
            "send_notification."
        )
        started = time.perf_counter()
        result: object = None
        error: Exception | None = None
        try:
            result = Runner.run_sync(
                self._agent,
                prompt,
                context=context,
                max_turns=self._settings.max_turns,
            )
        except Exception as exc:  # captured; surfaced after tracing
            error = exc

        self.last_tool_sequence = list(context.tool_calls)
        logger.info("agent tool sequence: %s", " -> ".join(self.last_tool_sequence) or "<none>")
        self._emit_trace(context, result, email, pdf_path, started, error)

        if context.notification is not None:
            return context.notification
        if error is not None:
            raise AgentRunError(f"Agent run failed: {type(error).__name__}: {error}") from error
        raise AgentRunError("The agent finished without sending a notification.")

    def _emit_trace(
        self,
        context: _ToolContext,
        result: object,
        email: InboundEmail,
        pdf_path: Path,
        started: float,
        error: Exception | None,
    ) -> None:
        try:
            latency_ms = (time.perf_counter() - started) * 1000.0
            agent_usage = _agent_usage(result)
            vision_usage = getattr(self._extractor, "last_usage", None) or TokenUsage()
            total = agent_usage + vision_usage
            cost = estimate_cost(vision_usage, self._settings.extractor_model) + estimate_cost(
                agent_usage, self._settings.agent_model
            )
            notification = context.notification
            payload = notification.payload if notification is not None else None
            coverage = float(payload.field_coverage_pct) if payload is not None else 0.0
            validation = 1.0 if (payload is not None and payload.validation_passed) else 0.0
            n_warnings = len(payload.warnings) if payload is not None else 0

            metrics: dict[str, float] = {
                "prompt_tokens": float(total.input_tokens),
                "completion_tokens": float(total.output_tokens),
                "total_tokens": float(total.total_tokens),
                "requests": float(total.requests),
                "estimated_cost_usd": round(cost, 6),
                "latency_ms": round(latency_ms, 1),
                "field_coverage_pct": coverage,
                "validation_passed": validation,
                "num_tool_calls": float(len(self.last_tool_sequence)),
                "num_warnings": float(n_warnings),
            }
            for tool_name, milliseconds in context.timings.items():
                metrics[f"{tool_name}_latency_ms"] = round(milliseconds, 1)

            if self._settings.enable_judge and notification is not None and payload is not None:
                score = judge_faithfulness(
                    notification.summary, payload.model_dump_json(), self._settings
                )
                if score is not None:
                    metrics["judge_faithfulness"] = score

            params = {
                "extractor_model": self._settings.extractor_model,
                "agent_model": self._settings.agent_model,
                "max_turns": self._settings.max_turns,
                "render_dpi": self._settings.render_dpi,
                "max_pages": self._settings.max_pages,
            }
            status = "ok"
            if error is not None:
                status = "partial" if notification is not None else "error"
            tags = {
                "tool_sequence": " -> ".join(self.last_tool_sequence) or "<none>",
                "status": status,
                "invoice_number_present": str(bool(payload is not None and payload.invoice_number)),
                "email_sha256": _sha256_text(f"{email.subject}|{email.from_}|{email.body}"),
                "pdf_sha256": _sha256_file(pdf_path),
            }
            with self._tracer.start_run("process_invoice") as run_handle:
                run_handle.log_params(params)
                run_handle.log_metrics(metrics)
                run_handle.set_tags(tags)
        except Exception:
            logger.debug("tracing failed (non-fatal)", exc_info=True)
