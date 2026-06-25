"""OpenAI Agents SDK runner with exactly two tools.

The agent orchestrates the required tool sequence: ``extract_invoice_data`` then
``send_notification``. Tool dependencies (extractor, notifier, the loaded email and the
resolved PDF path) are passed via the run *context*, so the tools take no large arguments
and the LLM only composes the human-readable summary.
"""

from __future__ import annotations

import logging
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
from invoice_agent.domain.ports import InvoiceExtractor, NotificationSender
from invoice_agent.infrastructure.notifier import render_summary

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


@function_tool
def extract_invoice_data(ctx: RunContextWrapper[_ToolContext]) -> str:
    """Extract structured invoice fields from the attached PDF, including fields that appear
    only inside embedded images (such as the invoice number). Returns the extracted invoice
    data as a JSON string. Call this exactly once, before send_notification."""
    context = ctx.context
    context.tool_calls.append("extract_invoice_data")
    data = context.extractor.extract(context.pdf_path, context.email)
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
    text = (summary or "").strip() or render_summary(context.extracted)
    notification = OutboundNotification(summary=text, payload=context.extracted)
    confirmation = context.notifier.send(notification)
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


class AgentInvoiceRunner:
    """``InvoiceAgentRunner`` adapter built on the OpenAI Agents SDK."""

    def __init__(
        self,
        settings: Settings,
        extractor: InvoiceExtractor,
        notifier: NotificationSender,
    ) -> None:
        self._settings = settings
        self._extractor = extractor
        self._notifier = notifier
        self.last_tool_sequence: list[str] = []

        if settings.openai_api_key:
            set_default_openai_key(settings.openai_api_key, use_for_tracing=False)
        # SDK-native tracing is disabled here; MLflow tracing is added in Phase 3.
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
        try:
            Runner.run_sync(
                self._agent,
                prompt,
                context=context,
                max_turns=self._settings.max_turns,
            )
        except Exception as exc:
            self.last_tool_sequence = list(context.tool_calls)
            if context.notification is not None:
                logger.info("agent tool sequence: %s", " -> ".join(self.last_tool_sequence))
                return context.notification
            raise AgentRunError(f"Agent run failed: {type(exc).__name__}: {exc}") from exc

        self.last_tool_sequence = list(context.tool_calls)
        logger.info("agent tool sequence: %s", " -> ".join(self.last_tool_sequence) or "<none>")
        if context.notification is None:
            raise AgentRunError("The agent finished without sending a notification.")
        return context.notification
