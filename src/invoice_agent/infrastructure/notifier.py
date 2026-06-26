"""
File-writing notification adapter + deterministic summary renderer.

``send`` writes the human-readable summary (``outbound_email.txt``) and the structured
payload (``outbound_email.json``) to the configured output directory. ``render_summary``
builds a sectioned Customer Service summary and is also used as a deterministic fallback
when the agent does not supply one.
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Optional

from invoice_agent.config import Settings
from invoice_agent.domain.models import ApprovalDecision, InvoiceData, OutboundNotification

logger = logging.getLogger(__name__)

_TXT_FILENAME = "outbound_email.txt"
_JSON_FILENAME = "outbound_email.json"


class NotificationError(Exception):
    """Raised when the notification files cannot be written."""


class FileNotificationSender:
    """``NotificationSender`` that writes the summary + JSON payload to disk."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def send(self, notification: OutboundNotification) -> str:
        out_dir = self._settings.output_dir
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise NotificationError(f"Could not create output directory '{out_dir}': {exc}") from exc

        txt_path = out_dir / _TXT_FILENAME
        json_path = out_dir / _JSON_FILENAME
        if notification.decision is not None:
            body = render_decision_card(notification.decision) + "\n\n" + notification.summary
        else:
            body = notification.summary
        try:
            txt_path.write_text(body, encoding="utf-8")
            json_path.write_text(
                notification.model_dump_json(indent=2),
                encoding="utf-8",
            )
        except (OSError, ValueError) as exc:
            raise NotificationError(f"Could not write notification files: {exc}") from exc

        logger.info("Notification written to %s and %s", txt_path.name, json_path.name)
        return f"Notification written to {txt_path} and {json_path}."


def _fmt_money(value: Optional[Decimal]) -> str:
    return "N/A" if value is None else f"{value}"


def render_decision_card(decision: ApprovalDecision) -> str:
    """Render the Human-in-the-Loop decision card prepended to the notification text."""
    banner = {
        "AUTO_APPROVED": "AUTO-APPROVED - WITHIN AUTHORITY",
        "APPROVAL_REQUIRED": "ACTION REQUIRED - APPROVAL NEEDED",
        "ON_HOLD": "ON HOLD - HUMAN REVIEW REQUIRED",
    }.get(decision.status.value, decision.status.value)

    lines: list[str] = [
        "=" * 48,
        f"DECISION: {banner}",
        "=" * 48,
        f"- Acting persona: {decision.acting_persona}",
        f"- Invoice total: {_fmt_money(decision.invoice_total)}",
        f"- Approval limit: {_fmt_money(decision.approval_limit)}",
        f"- Reason: {decision.reason}",
        f"- Required action: {decision.required_action}",
        "",
        "POLICY CHECKS",
    ]
    for check in decision.checks:
        mark = "PASS" if check.passed else "FAIL"
        detail = f" - {check.detail}" if check.detail else ""
        lines.append(f"- [{mark}] {check.name}{detail}")
    return "\n".join(lines)


def render_summary(data: InvoiceData) -> str:
    """Build a sectioned, human-readable Customer Service summary from ``InvoiceData``."""
    lines: list[str] = [
        "INVOICE INTAKE — CUSTOMER SERVICE NOTIFICATION",
        "=" * 48,
        "",
        "VENDOR & INVOICE",
        f"- Vendor: {data.vendor_name or 'N/A'}",
        f"- Invoice #: {data.invoice_number or 'N/A'}",
        f"- Customer PO: {data.customer_po_number or 'N/A'}",
        f"- Invoice date: {data.invoice_date or 'N/A'}",
        f"- Due date: {data.due_date or 'N/A'}",
        f"- Payment terms: {data.payment_terms or 'N/A'}",
        f"- Currency: {data.currency or 'N/A'}",
        "",
        "AMOUNTS",
        f"- Subtotal: {_fmt_money(data.subtotal)}",
    ]
    for tax in data.taxes:
        rate = f" @ {tax.rate}" if tax.rate is not None else ""
        lines.append(f"- Tax ({tax.jurisdiction or 'N/A'}{rate}): {_fmt_money(tax.amount)}")
    lines.append(f"- Total due: {_fmt_money(data.total_due)}")
    lines.append("")

    if data.line_items:
        lines.append("LINE ITEMS")
        for item in data.line_items:
            qty = item.quantity if item.quantity is not None else "N/A"
            lines.append(
                f"- {item.sku or 'N/A'} | {item.description or ''} | qty {qty} | "
                f"unit {_fmt_money(item.unit_price)} | total {_fmt_money(item.line_total)}"
            )
        lines.append("")

    if data.ship_to:
        lines.append("SHIP-TO / SITE ALLOCATIONS")
        for site in data.ship_to:
            parts = [site.site_name or "N/A"]
            if site.cost_centre:
                parts.append(f"cost centre {site.cost_centre}")
            if site.address:
                parts.append(site.address)
            if site.allocation:
                parts.append(f"allocation: {', '.join(site.allocation)}")
            lines.append("- " + " | ".join(parts))
        lines.append("")

    if data.notes:
        lines.append("NOTES")
        lines.extend(f"- {note}" for note in data.notes)
        lines.append("")

    if data.warnings:
        lines.append("WARNINGS")
        lines.extend(f"- {warning}" for warning in data.warnings)
        lines.append("")

    lines.append(
        f"Field coverage: {data.field_coverage_pct:.0f}%  |  "
        f"Validation passed: {data.validation_passed}"
    )
    return "\n".join(lines).rstrip() + "\n"
