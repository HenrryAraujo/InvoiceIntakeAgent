"""PDF extractor tests with a mocked vision client (no network)."""

import json
from decimal import Decimal

from invoice_agent.domain.models import InboundEmail
from invoice_agent.infrastructure.pdf_extractor import PdfInvoiceExtractor


def _email(body: str = "") -> InboundEmail:
    return InboundEmail(subject="s", from_="x@y.test", body=body)


def test_extract_maps_fields(settings, synthetic_pdf, fake_invoice_json, make_fake_openai):
    client = make_fake_openai(fake_invoice_json)
    extractor = PdfInvoiceExtractor(settings, client=client)
    data = extractor.extract(synthetic_pdf, _email())

    assert data.vendor_name == "Synthetic Vendor Inc."
    assert data.invoice_number == "SY-0001"
    assert data.subtotal == Decimal("1000.00")
    assert [t.jurisdiction for t in data.taxes] == ["ON", "QC"]
    assert data.ship_to[0].allocation == ["A-1 Qty 10"]
    assert data.validation_passed is True
    assert data.field_coverage_pct > 0
    assert extractor.last_usage.input_tokens > 0
    assert len(client.responses.calls) == 1  # exactly one vision call


def test_missing_invoice_number_sets_validation_false(settings, synthetic_pdf, make_fake_openai):
    payload = json.dumps({"vendor_name": "V", "invoice_number": None})
    extractor = PdfInvoiceExtractor(settings, client=make_fake_openai(payload))
    data = extractor.extract(synthetic_pdf, _email())
    assert data.invoice_number is None
    assert data.validation_passed is False
    assert any("invoice_number" in w for w in data.warnings)


def test_duplicate_note_is_guaranteed(settings, synthetic_pdf, make_fake_openai):
    payload = json.dumps({"invoice_number": "X", "notes": []})
    extractor = PdfInvoiceExtractor(settings, client=make_fake_openai(payload))
    data = extractor.extract(synthetic_pdf, _email(body="This may be a duplicate of the December quote."))
    assert any("duplicat" in n.lower() for n in data.notes)


def test_decimal_parsed_exactly_from_json_number(settings, synthetic_pdf, make_fake_openai):
    payload = json.dumps({"invoice_number": "X", "total_due": 129150.06})
    extractor = PdfInvoiceExtractor(settings, client=make_fake_openai(payload))
    data = extractor.extract(synthetic_pdf, _email())
    assert data.total_due == Decimal("129150.06")


def test_vision_failure_degrades_gracefully(settings, synthetic_pdf, make_fake_openai):
    extractor = PdfInvoiceExtractor(settings, client=make_fake_openai("", raises=RuntimeError("boom")))
    data = extractor.extract(synthetic_pdf, _email())
    assert data.validation_passed is False
    assert data.warnings


def test_unreadable_pdf_degrades(settings, tmp_path, make_fake_openai):
    bad = tmp_path / "data" / "bad.pdf"
    bad.parent.mkdir(parents=True, exist_ok=True)
    bad.write_text("not a real pdf", encoding="utf-8")
    extractor = PdfInvoiceExtractor(settings, client=make_fake_openai("{}"))
    data = extractor.extract(bad, _email())
    assert data.validation_passed is False
    assert data.warnings
