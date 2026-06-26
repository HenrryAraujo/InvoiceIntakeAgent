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
    monkeypatch.setattr(cli, "build_use_case", lambda settings, email: _FakeUseCase(notification))

    rc = cli.main(["--email", "x.json"])

    assert rc == 0
    assert "SUMMARY-LINE" in capsys.readouterr().out


def test_cli_missing_key_returns_2(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "get_settings", lambda: _settings(tmp_path, key=None))

    rc = cli.main(["--email", "x.json"])

    assert rc == 2
    assert "OPENAI_API_KEY" in capsys.readouterr().err


def test_cli_unexpected_error_is_clean(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "get_settings", lambda: _settings(tmp_path))

    def _boom(settings, email):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(cli, "build_use_case", _boom)

    rc = cli.main(["--email", "x.json"])

    err = capsys.readouterr().err
    assert rc == 1
    assert "kaboom" in err
    assert "Traceback" not in err  # no stack-trace leakage
