"""Application use case: orchestrates ports only (no I/O, no infrastructure imports)."""

from __future__ import annotations

from invoice_agent.domain.models import OutboundNotification
from invoice_agent.domain.ports import InboundEmailSource, InvoiceAgentRunner


class ProcessInvoiceUseCase:
    """Loads the inbound email, resolves the PDF, and runs the agent."""

    def __init__(
        self,
        email_source: InboundEmailSource,
        agent_runner: InvoiceAgentRunner,
    ) -> None:
        self._email_source = email_source
        self._agent_runner = agent_runner

    def execute(self) -> OutboundNotification:
        email = self._email_source.load()
        pdf_path = self._email_source.resolve_attachment(email)
        return self._agent_runner.run(email, pdf_path)
