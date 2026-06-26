"""Interface tests: FastAPI TestClient with the use case overridden via DI (no network)."""

from decimal import Decimal

from fastapi.testclient import TestClient

from invoice_agent.config import Settings
from invoice_agent.domain.models import InvoiceData, OutboundNotification, TaxBreakdown
from invoice_agent.interface.api import app, get_default_use_case, settings_dependency


class _FakeUseCase:
    def execute(self) -> OutboundNotification:
        payload = InvoiceData(
            vendor_name="ACME",
            invoice_number="INV-1",
            subtotal=Decimal("100.00"),
            taxes=[TaxBreakdown(jurisdiction="ON", amount=Decimal("13.00"))],
            validation_passed=True,
            field_coverage_pct=50.0,
        )
        return OutboundNotification(summary="summary", payload=payload)


def _client() -> TestClient:
    app.dependency_overrides[get_default_use_case] = lambda: _FakeUseCase()
    app.dependency_overrides[settings_dependency] = lambda: Settings(
        openai_api_key="test", enable_tracing=False
    )
    return TestClient(app)


def teardown_function(_func) -> None:
    app.dependency_overrides.clear()


def test_health_returns_ok():
    response = _client().get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_process_invoice_default_path():
    response = _client().post("/process-invoice")
    assert response.status_code == 200
    body = response.json()
    assert body["summary"] == "summary"
    assert body["payload"]["invoice_number"] == "INV-1"
    assert body["payload"]["subtotal"] == "100.00"  # Decimal serialized as string


def test_process_invoice_one_file_is_rejected():
    response = _client().post(
        "/process-invoice", files={"email": ("e.json", b"{}", "application/json")}
    )
    assert response.status_code == 400
