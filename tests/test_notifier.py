"""Notifier tests: file output + summary rendering."""

import json
from decimal import Decimal

import pytest

from invoice_agent.domain.models import (
    InvoiceData,
    LineItem,
    OutboundNotification,
    ShipTo,
    TaxBreakdown,
)
from invoice_agent.infrastructure.notifier import (
    FileNotificationSender,
    NotificationError,
    render_summary,
)


def _sample() -> InvoiceData:
    return InvoiceData(
        vendor_name="Vendor",
        invoice_number="INV-1",
        customer_po_number="PO-1",
        payment_terms="Net 30",
        currency="CAD",
        subtotal=Decimal("1000.00"),
        taxes=[
            TaxBreakdown(jurisdiction="ON", rate=Decimal("13"), amount=Decimal("130.00")),
            TaxBreakdown(jurisdiction="QC", rate=Decimal("9.975"), amount=Decimal("99.75")),
        ],
        total_due=Decimal("1229.75"),
        line_items=[
            LineItem(
                sku="A-1", description="Item", quantity=Decimal("10"),
                unit_price=Decimal("100.00"), line_total=Decimal("1000.00"),
            )
        ],
        ship_to=[ShipTo(site_name="Site A", cost_centre="CC-1", allocation=["A-1 Qty 10"])],
        notes=["Appointment required"],
        field_coverage_pct=100.0,
    )


def test_send_writes_both_files(settings):
    data = _sample()
    confirmation = FileNotificationSender(settings).send(
        OutboundNotification(summary=render_summary(data), payload=data)
    )
    txt = settings.output_dir / "outbound_email.txt"
    js = settings.output_dir / "outbound_email.json"
    assert txt.is_file() and js.is_file()
    assert "written" in confirmation.lower()

    back = InvoiceData.model_validate(json.loads(js.read_text(encoding="utf-8")))
    assert back.invoice_number == "INV-1"
    assert [t.jurisdiction for t in back.taxes] == ["ON", "QC"]
    assert back.ship_to[0].allocation == ["A-1 Qty 10"]


def test_send_creates_missing_output_dir(settings):
    assert not settings.output_dir.exists()
    FileNotificationSender(settings).send(OutboundNotification(summary="x", payload=InvoiceData()))
    assert settings.output_dir.exists()


def test_render_summary_sections_and_joined_allocation():
    text = render_summary(_sample())
    assert "VENDOR & INVOICE" in text
    assert "INV-1" in text
    assert "Tax (ON" in text and "Tax (QC" in text
    assert "allocation: A-1 Qty 10" in text  # joined list, not a python repr
    assert "['" not in text


def test_send_write_failure_raises_notification_error(settings, tmp_path):
    blocker = tmp_path / "blocker"
    blocker.write_text("x", encoding="utf-8")  # a FILE where a directory is expected
    blocked = settings.model_copy(update={"output_dir": blocker / "output"})
    with pytest.raises(NotificationError):
        FileNotificationSender(blocked).send(
            OutboundNotification(summary="x", payload=InvoiceData())
        )
