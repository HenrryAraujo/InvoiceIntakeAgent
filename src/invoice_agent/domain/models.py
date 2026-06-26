"""
Domain models (Pydantic v2 value objects / entities).

Monetary values and quantities use ``Decimal`` to avoid rounding drift. All extracted
invoice fields are optional/nullable; lenient *before* validators keep extraction robust
against the minor type inconsistencies a vision model can produce (so a single odd value
never crashes validation of the whole payload).
"""

from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Annotated, Optional

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field


def _as_str(value: object) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, str):
        return value.strip() or None
    return str(value)


def _as_decimal(value: object) -> Optional[Decimal]:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, (int, float)):
        return Decimal(str(value))
    if isinstance(value, str):
        cleaned = value.strip().replace(",", "").replace("$", "").replace("%", "")
        for token in ("CAD", "USD", "cad", "usd"):
            cleaned = cleaned.replace(token, "")
        cleaned = cleaned.strip()
        if not cleaned:
            return None
        try:
            return Decimal(cleaned)
        except InvalidOperation:
            return None
    return None


def _as_model_list(value: object) -> list:
    # Keep dicts (vision-model output) and BaseModel instances (direct construction);
    # drop anything else (e.g. stray strings) so nested validation never crashes.
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, (dict, BaseModel))]


def _as_str_list(value: object) -> list:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, list):
        return [str(item).strip() for item in value if item is not None and str(item).strip()]
    return []


OptStr = Annotated[Optional[str], BeforeValidator(_as_str)]
Money = Annotated[Optional[Decimal], BeforeValidator(_as_decimal)]
StrList = Annotated[list, BeforeValidator(_as_str_list)]


class LineItem(BaseModel):
    model_config = ConfigDict(extra="ignore")

    sku: OptStr = None
    description: OptStr = None
    quantity: Money = None
    unit_price: Money = None
    line_total: Money = None


class TaxBreakdown(BaseModel):
    model_config = ConfigDict(extra="ignore")

    jurisdiction: OptStr = None  # e.g. ON / QC
    rate: Money = None
    amount: Money = None


class ShipTo(BaseModel):
    model_config = ConfigDict(extra="ignore")

    site_name: OptStr = None
    address: OptStr = None
    cost_centre: OptStr = None
    allocation: StrList = Field(default_factory=list)


class Attachment(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: OptStr = None
    content_type: OptStr = None
    content_bytes: OptStr = None  # base64 when inline; null in the mock envelope


class InboundEmail(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    subject: OptStr = None
    from_: OptStr = Field(default=None, alias="from")
    to: StrList = Field(default_factory=list)
    cc: StrList = Field(default_factory=list)
    body: OptStr = None
    attachments: list[Attachment] = Field(default_factory=list)
    sent_at: OptStr = None


class InvoiceData(BaseModel):
    model_config = ConfigDict(extra="ignore")

    vendor_name: OptStr = None
    invoice_number: OptStr = None  # high priority; may exist only inside a PDF image
    invoice_date: OptStr = None
    due_date: OptStr = None
    payment_terms: OptStr = None
    currency: OptStr = None
    customer_po_number: OptStr = None
    subtotal: Money = None
    taxes: Annotated[list[TaxBreakdown], BeforeValidator(_as_model_list)] = Field(default_factory=list)
    total_due: Money = None
    line_items: Annotated[list[LineItem], BeforeValidator(_as_model_list)] = Field(default_factory=list)
    ship_to: Annotated[list[ShipTo], BeforeValidator(_as_model_list)] = Field(default_factory=list)
    notes: StrList = Field(default_factory=list)
    warnings: StrList = Field(default_factory=list)
    validation_passed: bool = True
    field_coverage_pct: float = 0.0


class Persona(BaseModel):
    """An acting Customer Service persona with a Delegation-of-Authority approval limit."""

    model_config = ConfigDict(extra="ignore")

    key: str
    title: str
    approval_limit: Money = None
    currency: str = "CAD"


class DecisionStatus(str, Enum):
    """Outcome of the Human-in-the-Loop approval gate."""

    AUTO_APPROVED = "AUTO_APPROVED"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    ON_HOLD = "ON_HOLD"


class CheckResult(BaseModel):
    """A single deterministic policy check shown on the decision card."""

    model_config = ConfigDict(extra="ignore")

    name: str
    passed: bool
    detail: OptStr = None


class ApprovalDecision(BaseModel):
    """Deterministic routing decision attached to an outbound notification."""

    model_config = ConfigDict(extra="ignore")

    status: DecisionStatus
    acting_persona: str
    approval_limit: Money = None
    invoice_total: Money = None
    reason: str
    required_action: str
    checks: list[CheckResult] = Field(default_factory=list)


class OutboundNotification(BaseModel):
    model_config = ConfigDict(extra="ignore")

    summary: str
    payload: InvoiceData
    decision: Optional[ApprovalDecision] = None
