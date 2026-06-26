"""FastAPI interface — HTTP parity with the CLI over the same ``ProcessInvoiceUseCase``.

- ``GET /health`` — liveness probe (200).
- ``POST /process-invoice`` — runs the agent on the mock inbound by default, or on an
  uploaded email JSON + PDF (multipart override). Returns ``{summary, payload}`` and writes
  the output files.

The use case is provided via a dependency (``get_default_use_case``) so tests can override
it with a fake. Endpoints are intentionally synchronous: FastAPI runs them in a worker
thread, so the agent's ``Runner.run_sync`` (which must not run inside a live event loop)
works directly.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Annotated, Optional

from fastapi import Depends, FastAPI, File, HTTPException, UploadFile

from invoice_agent.application.process_invoice import ProcessInvoiceUseCase
from invoice_agent.config import Settings, get_settings
from invoice_agent.domain.models import OutboundNotification
from invoice_agent.infrastructure.agent import AgentRunError
from invoice_agent.infrastructure.inbound_email import (
    AttachmentResolutionError,
    InboundEmailError,
    JsonFileInboundEmailSource,
)
from invoice_agent.infrastructure.notifier import NotificationError
from invoice_agent.interface.cli import build_use_case

_PDF_CONTENT_TYPES = {"application/pdf", "application/x-pdf"}

app = FastAPI(title="Invoice-Intake Agent", version="0.1.0")


def settings_dependency() -> Settings:
    return get_settings()


def get_default_use_case(
    settings: Annotated[Settings, Depends(settings_dependency)],
) -> ProcessInvoiceUseCase:
    """Build the default (mock inbound) use case over ``data/Email.json``.

    Declared as a dependency so tests can override it with a fake use case.
    """
    return build_use_case(settings, str(settings.input_dir / "Email.json"))


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/process-invoice", response_model=OutboundNotification)
def process_invoice(
    settings: Annotated[Settings, Depends(settings_dependency)],
    use_case: Annotated[ProcessInvoiceUseCase, Depends(get_default_use_case)],
    email: Annotated[Optional[UploadFile], File()] = None,
    pdf: Annotated[Optional[UploadFile], File()] = None,
) -> OutboundNotification:
    if (email is None) != (pdf is None):
        raise HTTPException(
            status_code=400,
            detail="Provide both 'email' and 'pdf' to override, or neither for the mock inbound.",
        )

    if email is None or pdf is None:
        return _execute(use_case)

    return _execute_override(settings, email, pdf)


def _execute(use_case: ProcessInvoiceUseCase) -> OutboundNotification:
    try:
        return use_case.execute()
    except (InboundEmailError, AttachmentResolutionError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except NotificationError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except AgentRunError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


def _execute_override(
    settings: Settings,
    email: UploadFile,
    pdf: UploadFile,
) -> OutboundNotification:
    tmp_dir = Path(tempfile.mkdtemp(prefix="invoice_agent_"))
    try:
        email_path = tmp_dir / "email.json"
        email_path.write_bytes(email.file.read())

        probe = JsonFileInboundEmailSource(email_path=email_path, input_dir=tmp_dir)
        try:
            parsed = probe.load()
        except InboundEmailError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        pdf_name = next(
            (
                Path(a.name).name
                for a in parsed.attachments
                if a.name
                and (
                    a.name.lower().endswith(".pdf")
                    or (a.content_type or "").lower() in _PDF_CONTENT_TYPES
                )
            ),
            None,
        )
        if pdf_name is None:
            raise HTTPException(
                status_code=400,
                detail="Uploaded email does not reference a PDF attachment.",
            )

        (tmp_dir / pdf_name).write_bytes(pdf.file.read())
        use_case = build_use_case(settings, str(email_path), input_dir=tmp_dir)
        return _execute(use_case)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
