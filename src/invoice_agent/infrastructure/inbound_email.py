"""
Mock inbound-email adapter.

Parses the provided Microsoft Graph message envelope (``Email.json``) into the domain
``InboundEmail`` and resolves the referenced PDF attachment to a local file under the
configured input directory, with strict path-safety checks.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from invoice_agent.domain.models import Attachment, InboundEmail

logger = logging.getLogger(__name__)

_PDF_SUFFIX = ".pdf"
_PDF_CONTENT_TYPES = {"application/pdf", "application/x-pdf"}


class InboundEmailError(Exception):
    """Raised when the inbound email cannot be loaded or parsed."""


class AttachmentResolutionError(Exception):
    """Raised when the PDF attachment cannot be safely resolved to a local file."""


class JsonFileInboundEmailSource:
    """Loads a local Graph message JSON and resolves its PDF attachment."""

    def __init__(self, email_path: Path, input_dir: Path) -> None:
        self._email_path = Path(email_path)
        self._input_dir = Path(input_dir)

    def load(self) -> InboundEmail:
        logger.info("Loading inbound email: %s", self._email_path.name)
        try:
            raw = json.loads(self._email_path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise InboundEmailError(f"Inbound email file not found: {self._email_path}") from exc
        except json.JSONDecodeError as exc:
            raise InboundEmailError(
                f"Inbound email file is not valid JSON: {self._email_path}"
            ) from exc
        return self._map(raw)

    @staticmethod
    def _addresses(recipients: object) -> list[str]:
        out: list[str] = []
        if isinstance(recipients, list):
            for recipient in recipients:
                if isinstance(recipient, dict):
                    address = ((recipient.get("EmailAddress") or {}).get("Address"))
                    if address:
                        out.append(address)
        return out

    def _map(self, raw: object) -> InboundEmail:
        if isinstance(raw, dict) and isinstance(raw.get("Message"), dict):
            msg = raw["Message"]
        elif isinstance(raw, dict):
            msg = raw
        else:
            raise InboundEmailError("Inbound email JSON does not contain a message object.")

        body_field = msg.get("Body")
        body = body_field.get("Content") if isinstance(body_field, dict) else body_field
        from_addr = ((msg.get("From") or {}).get("EmailAddress") or {}).get("Address")

        attachments: list[Attachment] = []
        for item in msg.get("Attachments") or []:
            if isinstance(item, dict):
                attachments.append(
                    Attachment(
                        name=item.get("Name"),
                        content_type=item.get("ContentType"),
                        content_bytes=item.get("ContentBytes"),
                    )
                )

        return InboundEmail(
            subject=msg.get("Subject"),
            from_=from_addr,
            to=self._addresses(msg.get("ToRecipients")),
            cc=self._addresses(msg.get("CcRecipients")),
            body=body,
            attachments=attachments,
            sent_at=msg.get("SentDateTime"),
        )

    def resolve_attachment(self, email: InboundEmail) -> Path:
        pdfs = [a for a in email.attachments if self._is_pdf(a)]
        if not pdfs:
            raise AttachmentResolutionError("No PDF attachment was found on the inbound email.")
        if len(pdfs) > 1:
            raise AttachmentResolutionError(
                f"Expected exactly one PDF attachment; found {len(pdfs)}."
            )
        resolved = self._safe_path(pdfs[0].name or "")
        logger.info("Resolved PDF attachment: %s", resolved.name)
        return resolved

    @staticmethod
    def _is_pdf(attachment: Attachment) -> bool:
        name = (attachment.name or "").lower()
        ctype = (attachment.content_type or "").lower()
        return name.endswith(_PDF_SUFFIX) or ctype in _PDF_CONTENT_TYPES

    def _safe_path(self, name: str) -> Path:
        # Reject any name that carries directory components (e.g. path traversal).
        safe_name = Path(name).name
        if not safe_name or safe_name != name:
            raise AttachmentResolutionError(f"Unsafe attachment name rejected: {name!r}")

        base = self._input_dir.resolve()
        target = (base / safe_name).resolve()
        if base != target.parent:
            raise AttachmentResolutionError("Resolved attachment path escapes the input directory.")
        if target.suffix.lower() != _PDF_SUFFIX:
            raise AttachmentResolutionError(f"Attachment is not a PDF: {safe_name}")
        if not target.is_file():
            raise AttachmentResolutionError(f"Attachment file is missing: {target}")
        return target
