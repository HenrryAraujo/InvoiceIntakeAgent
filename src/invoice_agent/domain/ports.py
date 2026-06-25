"""Port Protocols — the abstract boundaries the application depends on (DIP).

Concrete adapters in the infrastructure layer implement these; the use case never
imports infrastructure directly.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from invoice_agent.domain.models import InboundEmail, InvoiceData, OutboundNotification


@runtime_checkable
class InboundEmailSource(Protocol):
    """Loads the inbound email and resolves its PDF attachment to a local path."""

    def load(self) -> InboundEmail: ...

    def resolve_attachment(self, email: InboundEmail) -> Path: ...


@runtime_checkable
class InvoiceExtractor(Protocol):
    """Extracts structured invoice data from a PDF (text + embedded images)."""

    def extract(self, pdf_path: Path, email: InboundEmail) -> InvoiceData: ...


@runtime_checkable
class NotificationSender(Protocol):
    """Sends/persists the Customer Service notification; returns a confirmation."""

    def send(self, notification: OutboundNotification) -> str: ...


@runtime_checkable
class InvoiceAgentRunner(Protocol):
    """Runs the agent (extract -> notify) and returns the outbound notification."""

    def run(self, email: InboundEmail, pdf_path: Path) -> OutboundNotification: ...


@runtime_checkable
class RunTracer(Protocol):
    """Observability boundary (implemented in Phase 3 over MLflow)."""

    def start(self, name: str) -> "RunHandle": ...

    def log_metrics(self, metrics: dict[str, float]) -> None: ...


@runtime_checkable
class RunHandle(Protocol):
    def __enter__(self) -> "RunHandle": ...

    def __exit__(self, *exc_info: object) -> None: ...
