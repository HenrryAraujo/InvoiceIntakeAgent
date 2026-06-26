"""Shared pytest fixtures.

All fixtures use **synthetic** data — the real provided email/PDF content is never
embedded in tests (per the assignment). Tests are offline: the OpenAI vision client is
faked and MLflow tracing is disabled.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pymupdf
import pytest

from invoice_agent.config import Settings


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        openai_api_key="test-key",
        input_dir=tmp_path / "data",
        output_dir=tmp_path / "output",
        enable_tracing=False,
        enable_judge=False,
    )


@pytest.fixture
def synthetic_email_dict() -> dict:
    """A minimal, synthetic Microsoft Graph message envelope."""
    return {
        "Message": {
            "Subject": "Test invoice please process",
            "Body": {
                "ContentType": "Text",
                "Content": "Please process. This may be a duplicate of an earlier quote.",
            },
            "From": {"EmailAddress": {"Name": "Sender", "Address": "sender@example.test"}},
            "ToRecipients": [{"EmailAddress": {"Name": "AP", "Address": "ap@example.test"}}],
            "CcRecipients": [{"EmailAddress": {"Name": "CC One", "Address": "cc1@example.test"}}],
            "Attachments": [
                {
                    "@odata.type": "#microsoft.graph.fileAttachment",
                    "Name": "Invoice.pdf",
                    "ContentType": "application/pdf",
                    "ContentBytes": None,
                }
            ],
            "SentDateTime": "2026-01-26T10:14:52-05:00",
        }
    }


@pytest.fixture
def synthetic_pdf(tmp_path: Path) -> Path:
    """Create a tiny real (synthetic) PDF for extractor tests."""
    path = tmp_path / "data" / "Invoice.pdf"
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Synthetic invoice for tests")
    doc.save(path)
    doc.close()
    return path


@pytest.fixture
def fake_invoice_json() -> str:
    return json.dumps(
        {
            "vendor_name": "Synthetic Vendor Inc.",
            "invoice_number": "SY-0001",
            "invoice_date": "2026-01-26",
            "due_date": "2026-02-25",
            "payment_terms": "Net 30",
            "currency": "CAD",
            "customer_po_number": "PO-TEST-1",
            "subtotal": 1000.00,
            "total_due": 1279.75,
            "taxes": [
                {"jurisdiction": "ON", "rate": "13", "amount": 130.00},
                {"jurisdiction": "QC", "rate": "9.975", "amount": 149.75},
            ],
            "line_items": [
                {"sku": "A-1", "description": "Item", "quantity": 10,
                 "unit_price": 100.00, "line_total": 1000.00},
            ],
            "ship_to": [
                {"site_name": "Site A", "cost_centre": "CC-1", "allocation": ["A-1 Qty 10"]},
            ],
            "notes": ["Appointment required"],
            "warnings": [],
        }
    )


class FakeUsage:
    def __init__(self, input_tokens: int = 11, output_tokens: int = 7) -> None:
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.total_tokens = input_tokens + output_tokens


class _FakeResponse:
    def __init__(self, output_text: str, usage: Any) -> None:
        self.output_text = output_text
        self.usage = usage


class _FakeResponses:
    def __init__(self, output_text: str, usage: Any, raises: Exception | None) -> None:
        self._output_text = output_text
        self._usage = usage
        self._raises = raises
        self.calls: list[dict] = []

    def create(self, **kwargs: Any) -> _FakeResponse:
        self.calls.append(kwargs)
        if self._raises is not None:
            raise self._raises
        return _FakeResponse(self._output_text, self._usage)


class FakeOpenAIClient:
    """Stands in for ``openai.OpenAI``; only ``.responses.create`` is used."""

    def __init__(
        self,
        output_text: str,
        usage: Any | None = None,
        raises: Exception | None = None,
    ) -> None:
        self.responses = _FakeResponses(output_text, usage or FakeUsage(), raises)


@pytest.fixture
def make_fake_openai():
    def _make(output_text: str, usage: Any | None = None, raises: Exception | None = None):
        return FakeOpenAIClient(output_text, usage, raises)

    return _make
