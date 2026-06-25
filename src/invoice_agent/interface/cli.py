"""CLI entrypoint and composition root.

Wires the concrete adapters into ``ProcessInvoiceUseCase`` and runs a single invoice
through the agent. The same use case is reused by the FastAPI interface (Phase 2).
"""

from __future__ import annotations

import argparse
import logging
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
from invoice_agent.infrastructure.pdf_extractor import PdfInvoiceExtractor


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="invoice-agent",
        description="Invoice-intake agent — ingest an email + PDF and notify Customer Service.",
    )
    parser.add_argument(
        "--email",
        default="./data/Email.json",
        help="Path to the inbound email JSON (default: ./data/Email.json).",
    )
    return parser


def build_use_case(settings: Settings, email_path: str) -> ProcessInvoiceUseCase:
    """Composition root: construct adapters and inject them into the use case."""
    source = JsonFileInboundEmailSource(
        email_path=Path(email_path),
        input_dir=settings.input_dir,
    )
    extractor = PdfInvoiceExtractor(settings)
    notifier = FileNotificationSender(settings)
    runner = AgentInvoiceRunner(settings, extractor, notifier)
    return ProcessInvoiceUseCase(source, runner)


def _configure_logging() -> None:
    """Surface invoice_agent INFO logs (e.g. the agent tool sequence) on stdout."""
    app_logger = logging.getLogger("invoice_agent")
    if not app_logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter("%(message)s"))
        app_logger.addHandler(handler)
        app_logger.setLevel(logging.INFO)
        app_logger.propagate = False


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point. Returns a process exit code."""
    args = _build_parser().parse_args(argv)
    _configure_logging()

    try:
        settings: Settings = get_settings()
    except ValidationError as exc:
        print("Configuration error — startup aborted:", file=sys.stderr)
        print(exc, file=sys.stderr)
        return 2

    if not settings.openai_api_key:
        print(
            "error: OPENAI_API_KEY is not set. Copy .env.example to .env and add your key.",
            file=sys.stderr,
        )
        return 2

    try:
        use_case = build_use_case(settings, args.email)
        notification = use_case.execute()
    except (
        InboundEmailError,
        AttachmentResolutionError,
        NotificationError,
        AgentRunError,
    ) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(notification.summary)
    print(f"\n[written] {settings.output_dir / 'outbound_email.txt'}")
    print(f"[written] {settings.output_dir / 'outbound_email.json'}")
    return 0
