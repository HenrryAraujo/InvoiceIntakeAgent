"""Application use-case test with fake adapters (no network)."""

from pathlib import Path

from invoice_agent.application.process_invoice import ProcessInvoiceUseCase
from invoice_agent.domain.models import InboundEmail, InvoiceData, OutboundNotification


class _FakeSource:
    def __init__(self, email: InboundEmail, pdf_path: Path) -> None:
        self._email = email
        self._pdf_path = pdf_path
        self.resolved_for: InboundEmail | None = None

    def load(self) -> InboundEmail:
        return self._email

    def resolve_attachment(self, email: InboundEmail) -> Path:
        self.resolved_for = email
        return self._pdf_path


class _FakeRunner:
    def __init__(self, notification: OutboundNotification) -> None:
        self._notification = notification
        self.calls: list[tuple] = []

    def run(self, email: InboundEmail, pdf_path: Path) -> OutboundNotification:
        self.calls.append((email, pdf_path))
        return self._notification


def test_use_case_orchestrates_load_resolve_run():
    email = InboundEmail(subject="s")
    pdf = Path("x.pdf")
    notification = OutboundNotification(summary="done", payload=InvoiceData(invoice_number="N1"))
    source = _FakeSource(email, pdf)
    runner = _FakeRunner(notification)

    result = ProcessInvoiceUseCase(source, runner).execute()

    assert result is notification
    assert source.resolved_for is email
    assert runner.calls == [(email, pdf)]
