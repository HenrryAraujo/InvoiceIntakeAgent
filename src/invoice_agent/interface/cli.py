"""
CLI entrypoint and composition root.

Wires the concrete adapters into ``ProcessInvoiceUseCase`` and runs a single invoice
through the agent. The same use case is reused by the FastAPI interface (Phase 2).
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from pydantic import ValidationError

from invoice_agent.application.process_invoice import ProcessInvoiceUseCase
from invoice_agent.config import Settings, get_settings
from invoice_agent.infrastructure.agent import AgentInvoiceRunner, AgentRunError
from invoice_agent.infrastructure.inbound_email import (
    AttachmentResolutionError,
    InboundEmailError,
    JsonFileInboundEmailSource,
)
from invoice_agent.infrastructure.notifier import FileNotificationSender, NotificationError
from invoice_agent.infrastructure.observability import make_tracer
from invoice_agent.infrastructure.pdf_extractor import PdfInvoiceExtractor
from invoice_agent.logging_setup import configure_logging


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="invoice-agent",
        description="Invoice-intake agent — ingest an email + PDF and notify Customer Service.",
    )
    parser.add_argument(
        "--email",
        default="./input_data/Email.json",
        help="Path to the inbound email JSON (default: ./input_data/Email.json).",
    )
    parser.add_argument(
        "--persona",
        choices=["rep", "supervisor"],
        default=None,
        help="Acting approver persona for the approval decision "
        "(default: ACTIVE_PERSONA from settings).",
    )
    return parser


def build_use_case(
    settings: Settings,
    email_path: str,
    input_dir: Path | None = None,
    persona_key: str | None = None,
) -> ProcessInvoiceUseCase:
    """Composition root: construct adapters and inject them into the use case.

    ``input_dir`` overrides where the PDF attachment is resolved (used by the API's
    multipart path); it defaults to ``settings.input_dir``. ``persona_key`` selects the
    acting approver persona for the deterministic approval decision.
    """
    source = JsonFileInboundEmailSource(
        email_path=Path(email_path),
        input_dir=input_dir if input_dir is not None else settings.input_dir,
    )
    extractor = PdfInvoiceExtractor(settings)
    notifier = FileNotificationSender(settings)
    tracer = make_tracer(settings)
    persona = settings.resolve_persona(persona_key)
    runner = AgentInvoiceRunner(settings, extractor, notifier, tracer=tracer, persona=persona)
    return ProcessInvoiceUseCase(source, runner)


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point. Returns a process exit code."""
    args = _build_parser().parse_args(argv)

    try:
        settings: Settings = get_settings()
    except ValidationError as exc:
        print("Configuration error — startup aborted:", file=sys.stderr)
        print(exc, file=sys.stderr)
        return 2

    configure_logging(settings)

    if not settings.openai_api_key:
        print(
            "error: OPENAI_API_KEY is not set. Copy .env.example to .env and add your key.",
            file=sys.stderr,
        )
        return 2

    try:
        use_case = build_use_case(settings, args.email, persona_key=args.persona)
        notification = use_case.execute()
    except (
        InboundEmailError,
        AttachmentResolutionError,
        NotificationError,
        AgentRunError,
    ) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # last-resort guard: a clean message, never a raw traceback
        print(f"error: unexpected failure ({type(exc).__name__}): {exc}", file=sys.stderr)
        return 1

    if notification.decision is not None:
        decision = notification.decision
        print(f"[decision] {decision.status.value}: {decision.required_action}\n")
    print(notification.summary)
    print(f"\n[written] {settings.output_dir / 'outbound_email.txt'}")
    print(f"[written] {settings.output_dir / 'outbound_email.json'}")
    return 0
