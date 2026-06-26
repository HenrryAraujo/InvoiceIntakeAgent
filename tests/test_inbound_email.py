"""Inbound-email adapter tests: Graph-envelope parsing + safe attachment resolution."""

import json
from pathlib import Path

import pytest

from invoice_agent.infrastructure.inbound_email import (
    AttachmentResolutionError,
    InboundEmailError,
    JsonFileInboundEmailSource,
)


def _write(path: Path, obj: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj), encoding="utf-8")
    return path


def test_parses_graph_envelope(tmp_path, synthetic_email_dict):
    email_path = _write(tmp_path / "Email.json", synthetic_email_dict)
    email = JsonFileInboundEmailSource(email_path, tmp_path).load()
    assert email.subject == "Test invoice please process"
    assert email.from_ == "sender@example.test"
    assert email.to == ["ap@example.test"]
    assert email.cc == ["cc1@example.test"]
    assert email.attachments[0].name == "Invoice.pdf"
    assert email.attachments[0].content_bytes is None
    assert "duplicate" in (email.body or "").lower()


def test_tolerates_flat_message(tmp_path):
    flat = {"Subject": "Flat", "Attachments": [{"Name": "Invoice.pdf", "ContentType": "application/pdf"}]}
    email = JsonFileInboundEmailSource(_write(tmp_path / "Email.json", flat), tmp_path).load()
    assert email.subject == "Flat"


def test_missing_file_raises(tmp_path):
    with pytest.raises(InboundEmailError):
        JsonFileInboundEmailSource(tmp_path / "nope.json", tmp_path).load()


def test_bad_json_raises(tmp_path):
    path = tmp_path / "Email.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(InboundEmailError):
        JsonFileInboundEmailSource(path, tmp_path).load()


def test_resolve_attachment_ok(tmp_path, synthetic_email_dict):
    email_path = _write(tmp_path / "Email.json", synthetic_email_dict)
    (tmp_path / "Invoice.pdf").write_bytes(b"%PDF-1.4 test")
    src = JsonFileInboundEmailSource(email_path, tmp_path)
    resolved = src.resolve_attachment(src.load())
    assert resolved.name == "Invoice.pdf"
    assert resolved.is_file()


def test_resolve_missing_pdf_file(tmp_path, synthetic_email_dict):
    email_path = _write(tmp_path / "Email.json", synthetic_email_dict)  # PDF not created
    src = JsonFileInboundEmailSource(email_path, tmp_path)
    with pytest.raises(AttachmentResolutionError):
        src.resolve_attachment(src.load())


def test_resolve_no_pdf_attachment(tmp_path):
    obj = {"Message": {"Subject": "x", "Attachments": [{"Name": "note.txt", "ContentType": "text/plain"}]}}
    src = JsonFileInboundEmailSource(_write(tmp_path / "Email.json", obj), tmp_path)
    with pytest.raises(AttachmentResolutionError):
        src.resolve_attachment(src.load())


def test_resolve_rejects_path_traversal(tmp_path):
    obj = {"Message": {"Attachments": [{"Name": "../evil.pdf", "ContentType": "application/pdf"}]}}
    src = JsonFileInboundEmailSource(_write(tmp_path / "Email.json", obj), tmp_path)
    with pytest.raises(AttachmentResolutionError):
        src.resolve_attachment(src.load())


def test_resolve_rejects_multiple_pdfs(tmp_path):
    obj = {
        "Message": {
            "Attachments": [
                {"Name": "a.pdf", "ContentType": "application/pdf"},
                {"Name": "b.pdf", "ContentType": "application/pdf"},
            ]
        }
    }
    src = JsonFileInboundEmailSource(_write(tmp_path / "Email.json", obj), tmp_path)
    with pytest.raises(AttachmentResolutionError):
        src.resolve_attachment(src.load())
