"""
Deterministic Human-in-the-Loop approval decision (Delegation of Authority).

Given the extracted invoice and the acting persona's authority limit, decide whether the
invoice is auto-approved, must be escalated for approval, or held for human review. This is
a **pure, deterministic** policy — a financial gate must never depend on an LLM, so it stays
auditable and free of model cost.
"""

from __future__ import annotations

from decimal import Decimal

from invoice_agent.domain.models import (
    ApprovalDecision,
    CheckResult,
    DecisionStatus,
    InvoiceData,
    Persona,
)

_DUPLICATE_MARKER = "duplicat"


def _has_duplicate_flag(invoice: InvoiceData) -> bool:
    blobs = [*(invoice.notes or []), *(invoice.warnings or [])]
    return any(_DUPLICATE_MARKER in (text or "").lower() for text in blobs)


def _tax_reconciles(invoice: InvoiceData, tolerance: Decimal) -> bool | None:
    """True/False if reconciliation can be checked; None when inputs are missing."""
    if invoice.subtotal is None or invoice.total_due is None:
        return None
    tax_sum = sum((t.amount for t in invoice.taxes if t.amount is not None), Decimal("0"))
    return abs((invoice.subtotal + tax_sum) - invoice.total_due) <= tolerance


def evaluate_approval(
    invoice: InvoiceData,
    persona: Persona,
    *,
    hold_on_duplicate: bool = False,
    tax_tolerance: Decimal = Decimal("0.01"),
    escalation_contact: str = "a supervisor",
) -> ApprovalDecision:
    """Return the approval decision for ``invoice`` under ``persona``'s authority."""
    currency = persona.currency
    total = invoice.total_due
    limit = persona.approval_limit
    total_present = total is not None
    recon = _tax_reconciles(invoice, tax_tolerance)
    duplicate = _has_duplicate_flag(invoice)
    within_authority = bool(total_present and limit is not None and total <= limit)

    checks = [
        CheckResult(
            name="Schema validation",
            passed=bool(invoice.validation_passed),
            detail="Invoice number and required fields present."
            if invoice.validation_passed
            else "Validation failed (e.g. invoice number missing).",
        ),
        CheckResult(
            name="Total present",
            passed=total_present,
            detail=f"Total due = {total} {currency}." if total_present else "Total due not extracted.",
        ),
        CheckResult(
            name="Tax reconciliation",
            passed=recon is True,
            detail="subtotal + taxes = total."
            if recon is True
            else (
                "subtotal + taxes does NOT equal total."
                if recon is False
                else "Could not verify (missing subtotal or total)."
            ),
        ),
        CheckResult(
            name="Duplicate check",
            passed=not duplicate,
            detail="Potential duplicate flagged — verify before payment."
            if duplicate
            else "No duplicate flag.",
        ),
        CheckResult(
            name="Authority limit",
            passed=within_authority,
            detail=(
                f"{total} {'<=' if within_authority else '>'} {limit} {currency}."
                if total_present
                else f"No total to compare against {limit} {currency}."
            ),
        ),
    ]

    integrity_ok = invoice.validation_passed and total_present and recon is True

    def _decision(status: DecisionStatus, reason: str, action: str) -> ApprovalDecision:
        return ApprovalDecision(
            status=status,
            acting_persona=persona.title,
            approval_limit=limit,
            invoice_total=total,
            reason=reason,
            required_action=action,
            checks=checks,
        )

    if not integrity_ok:
        reasons = []
        if not invoice.validation_passed:
            reasons.append("schema validation failed")
        if not total_present:
            reasons.append("total due missing")
        if recon is not True:
            reasons.append("taxes do not reconcile")
        return _decision(
            DecisionStatus.ON_HOLD,
            "Data integrity issue: " + "; ".join(reasons) + ".",
            "HOLD — resolve the flagged data issues and re-run before any approval.",
        )

    if duplicate and hold_on_duplicate:
        return _decision(
            DecisionStatus.ON_HOLD,
            "A potential duplicate was flagged.",
            "HOLD — confirm this is not a duplicate of an earlier document before approval.",
        )

    if within_authority:
        caution = (
            " Note: a potential duplicate was flagged — confirm before final payment."
            if duplicate
            else ""
        )
        return _decision(
            DecisionStatus.AUTO_APPROVED,
            f"Total {total} {currency} is within {persona.title}'s approval authority "
            f"({limit} {currency}).",
            f"Approved within authority — routed for processing.{caution}",
        )

    delta = (total - limit) if (total_present and limit is not None) else None
    return _decision(
        DecisionStatus.APPROVAL_REQUIRED,
        f"Total {total} {currency} exceeds {persona.title}'s approval limit "
        f"({limit} {currency}) by {delta} {currency}.",
        f"Request approval from {escalation_contact} before processing.",
    )
