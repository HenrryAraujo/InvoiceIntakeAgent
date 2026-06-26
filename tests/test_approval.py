"""Deterministic Human-in-the-Loop approval decision tests (no LLM, no network)."""

from decimal import Decimal

from invoice_agent.domain.approval import evaluate_approval
from invoice_agent.domain.models import (
    DecisionStatus,
    InvoiceData,
    Persona,
    TaxBreakdown,
)

REP = Persona(
    key="rep",
    title="Customer Service Representative",
    approval_limit=Decimal("10000"),
    currency="CAD",
)
SUPERVISOR = Persona(
    key="supervisor",
    title="Customer Service Supervisor",
    approval_limit=Decimal("150000"),
    currency="CAD",
)


def _valid_invoice(
    total: str,
    subtotal: str,
    taxes: list[tuple[str, str]],
    *,
    notes: list[str] | None = None,
    warnings: list[str] | None = None,
) -> InvoiceData:
    return InvoiceData(
        invoice_number="INV-1",
        subtotal=Decimal(subtotal),
        taxes=[TaxBreakdown(jurisdiction=j, amount=Decimal(a)) for j, a in taxes],
        total_due=Decimal(total),
        validation_passed=True,
        notes=notes or [],
        warnings=warnings or [],
    )


def test_within_authority_auto_approves():
    invoice = _valid_invoice("1130.00", "1000.00", [("ON", "130.00")])
    decision = evaluate_approval(invoice, REP)
    assert decision.status is DecisionStatus.AUTO_APPROVED
    assert decision.acting_persona == "Customer Service Representative"
    assert decision.approval_limit == Decimal("10000")


def test_over_limit_requires_approval_for_rep():
    invoice = _valid_invoice("129150.06", "114292.97", [("ON", "14857.09")])
    decision = evaluate_approval(invoice, REP, escalation_contact="Finance Manager")
    assert decision.status is DecisionStatus.APPROVAL_REQUIRED
    assert "Finance Manager" in decision.required_action


def test_same_invoice_supervisor_auto_approves():
    # The exact escalation scenario: rep -> approval needed; supervisor -> within authority.
    invoice = _valid_invoice("129150.06", "114292.97", [("ON", "14857.09")])
    assert evaluate_approval(invoice, REP).status is DecisionStatus.APPROVAL_REQUIRED
    assert evaluate_approval(invoice, SUPERVISOR).status is DecisionStatus.AUTO_APPROVED


def test_failed_validation_holds():
    invoice = InvoiceData(
        invoice_number=None,
        validation_passed=False,
        subtotal=Decimal("100"),
        total_due=Decimal("113"),
        taxes=[TaxBreakdown(jurisdiction="ON", amount=Decimal("13"))],
    )
    assert evaluate_approval(invoice, SUPERVISOR).status is DecisionStatus.ON_HOLD


def test_tax_mismatch_holds():
    invoice = _valid_invoice("9999.00", "1000.00", [("ON", "130.00")])  # 1130 != 9999
    decision = evaluate_approval(invoice, SUPERVISOR)
    assert decision.status is DecisionStatus.ON_HOLD
    assert any(not c.passed and "reconciliation" in c.name.lower() for c in decision.checks)


def test_missing_total_holds():
    invoice = InvoiceData(
        invoice_number="INV-1", validation_passed=True, subtotal=Decimal("1000.00")
    )
    assert evaluate_approval(invoice, SUPERVISOR).status is DecisionStatus.ON_HOLD


def test_duplicate_with_hold_flag_holds_even_within_authority():
    invoice = _valid_invoice(
        "1130.00", "1000.00", [("ON", "130.00")],
        warnings=["Possible duplicate of an earlier quote"],
    )
    decision = evaluate_approval(invoice, REP, hold_on_duplicate=True)
    assert decision.status is DecisionStatus.ON_HOLD


def test_duplicate_without_hold_flag_auto_approves_with_caution():
    invoice = _valid_invoice(
        "1130.00", "1000.00", [("ON", "130.00")], notes=["This may be a duplicate."]
    )
    decision = evaluate_approval(invoice, REP, hold_on_duplicate=False)
    assert decision.status is DecisionStatus.AUTO_APPROVED
    assert "duplicate" in decision.required_action.lower()


def test_all_named_checks_are_present():
    invoice = _valid_invoice("1130.00", "1000.00", [("ON", "130.00")])
    names = {c.name for c in evaluate_approval(invoice, REP).checks}
    assert {
        "Schema validation",
        "Total present",
        "Tax reconciliation",
        "Duplicate check",
        "Authority limit",
    } <= names
