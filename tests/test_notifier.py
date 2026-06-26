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
    render_decision_card,
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

    back = OutboundNotification.model_validate(json.loads(js.read_text(encoding="utf-8")))
    assert back.payload.invoice_number == "INV-1"
    assert [t.jurisdiction for t in back.payload.taxes] == ["ON", "QC"]
    assert back.payload.ship_to[0].allocation == ["A-1 Qty 10"]
    assert back.decision is None  # no decision supplied -> null in JSON


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


def test_send_includes_decision_card_and_full_json(settings):
    from invoice_agent.domain.models import ApprovalDecision, CheckResult, DecisionStatus

    data = _sample()
    decision = ApprovalDecision(
        status=DecisionStatus.APPROVAL_REQUIRED,
        acting_persona="Customer Service Representative",
        approval_limit=Decimal("10000"),
        invoice_total=Decimal("1229.75"),
        reason="exceeds limit",
        required_action="Request approval from Finance Manager.",
        checks=[CheckResult(name="Authority limit", passed=False, detail="over limit")],
    )
    FileNotificationSender(settings).send(
        OutboundNotification(summary=render_summary(data), payload=data, decision=decision)
    )
    txt = (settings.output_dir / "outbound_email.txt").read_text(encoding="utf-8")
    assert "ACTION REQUIRED" in txt
    assert "[FAIL] Authority limit" in txt
    assert "INVOICE INTAKE" in txt  # summary still present below the card
    js = json.loads((settings.output_dir / "outbound_email.json").read_text(encoding="utf-8"))
    assert js["decision"]["status"] == "APPROVAL_REQUIRED"
    assert js["payload"]["invoice_number"] == "INV-1"


def test_render_decision_card_marks_checks():
    from invoice_agent.domain.models import ApprovalDecision, CheckResult, DecisionStatus

    card = render_decision_card(
        ApprovalDecision(
            status=DecisionStatus.AUTO_APPROVED,
            acting_persona="Customer Service Supervisor",
            approval_limit=Decimal("150000"),
            invoice_total=Decimal("129150.06"),
            reason="within authority",
            required_action="Approved - routed for processing.",
            checks=[
                CheckResult(name="Authority limit", passed=True, detail="ok"),
                CheckResult(name="Duplicate check", passed=False, detail="maybe dup"),
            ],
        )
    )
    assert "AUTO-APPROVED" in card
    assert "[PASS] Authority limit" in card
    assert "[FAIL] Duplicate check" in card
