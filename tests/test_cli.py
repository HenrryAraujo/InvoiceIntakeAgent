"""CLI edge-case tests (offline): happy path, missing key, and clean unexpected-error handling."""

from invoice_agent.config import Settings
from invoice_agent.domain.models import InvoiceData, OutboundNotification
from invoice_agent.interface import cli


def _settings(tmp_path, key: str | None = "test-key") -> Settings:
    return Settings(
        openai_api_key=key,
        input_dir=tmp_path / "data",
        output_dir=tmp_path / "output",
        enable_tracing=False,
    )


class _FakeUseCase:
    def __init__(self, notification: OutboundNotification) -> None:
        self._notification = notification

    def execute(self) -> OutboundNotification:
        return self._notification


def test_cli_happy_path(tmp_path, monkeypatch, capsys):
    notification = OutboundNotification(
        summary="SUMMARY-LINE", payload=InvoiceData(invoice_number="N1")
    )
    monkeypatch.setattr(cli, "get_settings", lambda: _settings(tmp_path))
    monkeypatch.setattr(
        cli, "build_use_case", lambda settings, email, persona_key=None: _FakeUseCase(notification)
    )

    rc = cli.main(["--email", "x.json"])

    assert rc == 0
    assert "SUMMARY-LINE" in capsys.readouterr().out


def test_cli_persona_flag_and_decision_banner(tmp_path, monkeypatch, capsys):
    from invoice_agent.domain.models import ApprovalDecision, DecisionStatus

    decision = ApprovalDecision(
        status=DecisionStatus.APPROVAL_REQUIRED,
        acting_persona="Customer Service Representative",
        reason="over limit",
        required_action="Request approval from Finance Manager.",
    )
    notification = OutboundNotification(
        summary="SUMMARY-LINE", payload=InvoiceData(invoice_number="N1"), decision=decision
    )
    captured: dict = {}

    def _fake_build(settings, email, persona_key=None):
        captured["persona_key"] = persona_key
        return _FakeUseCase(notification)

    monkeypatch.setattr(cli, "get_settings", lambda: _settings(tmp_path))
    monkeypatch.setattr(cli, "build_use_case", _fake_build)

    rc = cli.main(["--email", "x.json", "--persona", "supervisor"])

    out = capsys.readouterr().out
    assert rc == 0
    assert captured["persona_key"] == "supervisor"
    assert "[decision] APPROVAL_REQUIRED" in out


def test_cli_missing_key_returns_2(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "get_settings", lambda: _settings(tmp_path, key=None))

    rc = cli.main(["--email", "x.json"])

    assert rc == 2
    assert "OPENAI_API_KEY" in capsys.readouterr().err


def test_cli_unexpected_error_is_clean(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "get_settings", lambda: _settings(tmp_path))

    def _boom(settings, email, persona_key=None):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(cli, "build_use_case", _boom)

    rc = cli.main(["--email", "x.json"])

    err = capsys.readouterr().err
    assert rc == 1
    assert "kaboom" in err
    assert "Traceback" not in err  # no stack-trace leakage
