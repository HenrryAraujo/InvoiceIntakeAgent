"""PDF invoice extractor adapter.

Loads the PDF with PyMuPDF (text per page + rendered page images, DPI/page capped) and
issues a **single** ``gpt-5`` vision call via the OpenAI Responses API to produce a
structured ``InvoiceData`` — including fields that exist only inside an embedded image
(e.g. the invoice number). No retries; failures degrade to partial data + warnings.
"""

from __future__ import annotations

import base64
import json
from decimal import Decimal
from pathlib import Path
from typing import Any, Optional

import pymupdf

from invoice_agent.config import Settings
from invoice_agent.domain.models import InboundEmail, InvoiceData

_PDF_TEXT_CAP = 6000
_BODY_CAP = 2000

_SYSTEM = (
    "You are an invoice data extraction engine. You receive a vendor invoice as PDF text plus "
    "rendered page images, and the inbound email that delivered it. Extract the requested fields "
    "and return ONLY a single JSON object. Read values that appear only inside the page images "
    "(for example an invoice number printed as an image). Use null for anything you cannot find. "
    "Never invent values. Monetary amounts and quantities must be plain numbers with no currency "
    "symbols or thousands separators (e.g. 1234.56). Keep all strings concise."
)

_SCHEMA_HINT = (
    "Return a JSON object with exactly these keys: "
    "vendor_name (string|null), invoice_number (string|null), invoice_date (string|null), "
    "due_date (string|null), payment_terms (string|null), currency (string|null), "
    "customer_po_number (string|null), subtotal (number|null), total_due (number|null), "
    "taxes (array of {jurisdiction, rate, amount}), "
    "line_items (array of {sku, description, quantity, unit_price, line_total}), "
    "ship_to (array of {site_name, address, cost_centre, allocation}), "
    "notes (array of strings: delivery windows, receiving requirements, duplicate warnings, "
    "cost centres), warnings (array of strings)."
)

_TARGET_FIELDS = (
    "vendor_name",
    "invoice_number",
    "invoice_date",
    "due_date",
    "payment_terms",
    "currency",
    "customer_po_number",
    "subtotal",
    "total_due",
)


def _coverage(data: InvoiceData) -> float:
    scalars = [getattr(data, name) for name in _TARGET_FIELDS]
    lists = [data.taxes, data.line_items, data.ship_to]
    present = sum(1 for value in scalars if value is not None) + sum(1 for value in lists if value)
    total = len(scalars) + len(lists)
    return round(100.0 * present / total, 1) if total else 0.0


class PdfInvoiceExtractor:
    """``InvoiceExtractor`` built on PyMuPDF + a single OpenAI Responses vision call."""

    def __init__(self, settings: Settings, client: Any | None = None) -> None:
        self._settings = settings
        self._client = client

    def _ensure_client(self) -> Any:
        if self._client is None:
            from openai import OpenAI

            self._client = OpenAI(api_key=self._settings.openai_api_key)
        return self._client

    def extract(self, pdf_path: Path, email: InboundEmail) -> InvoiceData:
        warnings: list[str] = []
        pdf_text, images = self._render(pdf_path, warnings)
        if not images:
            return InvoiceData(
                validation_passed=False,
                warnings=warnings or ["The PDF attachment could not be read."],
            )
        try:
            raw = self._vision_call(email, pdf_text, images)
        except Exception as exc:  # single call — surface as partial result, never crash
            warnings.append(f"Invoice extraction call failed: {type(exc).__name__}.")
            return InvoiceData(validation_passed=False, warnings=warnings)
        return self._map(raw, email, warnings)

    def _render(self, pdf_path: Path, warnings: list[str]) -> tuple[str, list[str]]:
        try:
            doc = pymupdf.open(pdf_path)
        except Exception:
            warnings.append(f"Could not open PDF attachment: {pdf_path.name}.")
            return "", []

        texts: list[str] = []
        images: list[str] = []
        max_pages = self._settings.max_pages
        try:
            for index, page in enumerate(doc):
                if index >= max_pages:
                    warnings.append(
                        f"PDF has more than {max_pages} pages; only the first {max_pages} "
                        "were analyzed."
                    )
                    break
                try:
                    texts.append(page.get_text())
                    pixmap = page.get_pixmap(dpi=self._settings.render_dpi)
                    images.append(base64.b64encode(pixmap.tobytes("png")).decode("ascii"))
                except Exception:
                    warnings.append(f"Page {index + 1} could not be rendered.")
        finally:
            doc.close()

        text = "\n".join(texts)
        if len(text) > _PDF_TEXT_CAP:
            text = text[:_PDF_TEXT_CAP]
        return text, images

    def _vision_call(self, email: InboundEmail, pdf_text: str, images: list[str]) -> Any:
        body = (email.body or "")[:_BODY_CAP]
        user_text = (
            "INBOUND EMAIL\n"
            f"Subject: {email.subject or ''}\n"
            f"From: {email.from_ or ''}\n"
            f"Body:\n{body}\n\n"
            "PDF TEXT (extracted):\n"
            f"{pdf_text or '(no embedded text)'}\n\n"
            f"{_SCHEMA_HINT}"
        )
        content: list[dict[str, Any]] = [{"type": "input_text", "text": user_text}]
        for encoded in images:
            content.append(
                {
                    "type": "input_image",
                    "image_url": f"data:image/png;base64,{encoded}",
                    "detail": "high",
                }
            )

        response = self._ensure_client().responses.create(
            model=self._settings.extractor_model,
            instructions=_SYSTEM,
            input=[{"role": "user", "content": content}],
            reasoning={"effort": "low"},
            text={"format": {"type": "json_object"}, "verbosity": "low"},
            max_output_tokens=8000,
            store=False,
        )
        return json.loads(response.output_text, parse_float=Decimal)

    def _map(self, raw: object, email: InboundEmail, warnings: list[str]) -> InvoiceData:
        if not isinstance(raw, dict):
            warnings.append("Extraction did not return a JSON object.")
            return InvoiceData(validation_passed=False, warnings=warnings)

        raw.pop("validation_passed", None)
        raw.pop("field_coverage_pct", None)
        try:
            data = InvoiceData.model_validate(raw)
        except Exception:
            warnings.append("Extracted data failed schema validation; returning a partial result.")
            data = InvoiceData()

        data.warnings = [*warnings, *data.warnings]
        self._ensure_duplicate_note(data, email)

        if not data.invoice_number:
            data.validation_passed = False
            data.warnings.append(
                "High-priority field 'invoice_number' was not extracted from text or images."
            )
        else:
            data.validation_passed = True

        data.field_coverage_pct = _coverage(data)
        return data

    @staticmethod
    def _ensure_duplicate_note(data: InvoiceData, email: InboundEmail) -> None:
        body = (email.body or "").lower()
        if "duplicat" in body and not any("duplicat" in note.lower() for note in data.notes):
            data.notes.append(
                "Email flags a potential duplicate (a preliminary quote was received earlier); "
                "verify before processing."
            )
