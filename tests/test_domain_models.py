"""Domain model tests: schema shape, coercion, Decimal integrity, tax/allocation structure."""

import json
from decimal import Decimal

from invoice_agent.domain.models import (
    ApprovalDecision,
    CheckResult,
    DecisionStatus,
    InboundEmail,
    InvoiceData,
    LineItem,
    OutboundNotification,
    Persona,
    ShipTo,
    TaxBreakdown,
)


def test_invoicedata_defaults_are_optional():
    data = InvoiceData()
    assert data.vendor_name is None
    assert data.invoice_number is None
    assert data.taxes == [] and data.line_items == [] and data.ship_to == []
    assert data.notes == [] and data.warnings == []
    assert data.validation_passed is True
    assert data.field_coverage_pct == 0.0


def test_money_coercion_handles_messy_strings():
    item = LineItem.model_validate(
        {"quantity": "10", "unit_price": "$1,234.56", "line_total": "12,345.60"}
    )
    assert item.quantity == Decimal("10")
    assert item.unit_price == Decimal("1234.56")
    assert item.line_total == Decimal("12345.60")


def test_money_coercion_drops_unparseable():
    item = LineItem.model_validate({"unit_price": "n/a", "line_total": None})
    assert item.unit_price is None
    assert item.line_total is None


def test_tax_rate_accepts_percent_sign():
    tax = TaxBreakdown.model_validate({"jurisdiction": "ON", "rate": "13%", "amount": 130})
    assert tax.jurisdiction == "ON"
    assert tax.rate == Decimal("13")
    assert tax.amount == Decimal("130")


def test_model_list_keeps_dicts_and_instances_drops_junk():
    data = InvoiceData.model_validate(
        {"taxes": [{"jurisdiction": "ON", "amount": 1}, "junk", 42, None]}
    )
    assert len(data.taxes) == 1
    assert data.taxes[0].jurisdiction == "ON"

    direct = InvoiceData(taxes=[TaxBreakdown(jurisdiction="QC", amount=Decimal("2"))])
    assert len(direct.taxes) == 1 and direct.taxes[0].jurisdiction == "QC"


def test_str_list_wraps_single_string():
    data = InvoiceData.model_validate({"notes": "single", "warnings": ["a", "b"]})
    assert data.notes == ["single"]
    assert data.warnings == ["a", "b"]


def test_allocation_is_a_list():
    site = ShipTo.model_validate({"site_name": "A", "allocation": ["x Qty 1", "y Qty 2"]})
    assert site.allocation == ["x Qty 1", "y Qty 2"]
    single = ShipTo.model_validate({"site_name": "B", "allocation": "all remaining"})
    assert single.allocation == ["all remaining"]


def test_inbound_email_from_alias():
    email = InboundEmail.model_validate({"subject": "s", "from": "x@y.test", "to": ["a@b.test"]})
    assert email.from_ == "x@y.test"
    assert email.to == ["a@b.test"]


def test_on_qc_tax_integrity_not_flattened():
    data = InvoiceData(
        subtotal=Decimal("1000.00"),
        taxes=[
            TaxBreakdown(jurisdiction="ON", rate=Decimal("13"), amount=Decimal("130.00")),
            TaxBreakdown(jurisdiction="QC", rate=Decimal("9.975"), amount=Decimal("99.75")),
        ],
        total_due=Decimal("1229.75"),
    )
    assert [t.jurisdiction for t in data.taxes] == ["ON", "QC"]
    tax_sum = sum((t.amount for t in data.taxes), Decimal("0"))
    assert tax_sum == Decimal("229.75")
    assert data.subtotal + tax_sum == data.total_due  # exact Decimal, no float drift


def test_line_item_totals_are_exact_decimal():
    items = [
        LineItem(quantity=Decimal("3"), unit_price=Decimal("0.10"), line_total=Decimal("0.30")),
        LineItem(quantity=Decimal("120"), unit_price=Decimal("357.88"), line_total=Decimal("42945.60")),
    ]
    assert items[0].quantity * items[0].unit_price == items[0].line_total
    assert sum((i.line_total for i in items), Decimal("0")) == Decimal("42945.90")


def test_json_roundtrip_decimal_as_string_and_allocation_array():
    data = InvoiceData(
        subtotal=Decimal("113983.69"),
        ship_to=[ShipTo(site_name="A", allocation=["x Qty 1"])],
    )
    raw = json.loads(data.model_dump_json())
    assert raw["subtotal"] == "113983.69"
    assert raw["ship_to"][0]["allocation"] == ["x Qty 1"]
    assert InvoiceData.model_validate(raw).subtotal == Decimal("113983.69")


def test_outbound_notification_shape():
    notification = OutboundNotification(summary="hi", payload=InvoiceData(invoice_number="N1"))
    body = json.loads(notification.model_dump_json())
    assert body["summary"] == "hi"
    assert body["payload"]["invoice_number"] == "N1"
    assert body["decision"] is None  # absent unless an approval decision is attached


def test_persona_and_decision_shape():
    persona = Persona(key="rep", title="Customer Service Representative",
                      approval_limit=Decimal("10000"), currency="CAD")
    assert persona.approval_limit == Decimal("10000")

    decision = ApprovalDecision(
        status=DecisionStatus.APPROVAL_REQUIRED,
        acting_persona=persona.title,
        approval_limit=persona.approval_limit,
        invoice_total=Decimal("129150.06"),
        reason="exceeds limit",
        required_action="Request approval from Finance Manager.",
        checks=[CheckResult(name="Authority limit", passed=False, detail="over limit")],
    )
    notification = OutboundNotification(
        summary="hi", payload=InvoiceData(invoice_number="N1"), decision=decision
    )
    body = json.loads(notification.model_dump_json())
    assert body["decision"]["status"] == "APPROVAL_REQUIRED"
    assert body["decision"]["invoice_total"] == "129150.06"  # Decimal serialized as string
    assert body["decision"]["checks"][0]["passed"] is False
