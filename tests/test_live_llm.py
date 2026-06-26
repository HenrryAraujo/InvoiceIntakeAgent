"""Opt-in live-LLM test.

Skipped unless ``OPENAI_API_KEY`` is present in the environment (protects credits). Note:
a ``.env`` file is NOT auto-loaded into the environment for pytest, so this stays skipped
by default even when a key is configured for normal runs.
"""

import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.live


@pytest.mark.skipif(
    not os.getenv("OPENAI_API_KEY"),
    reason="requires OPENAI_API_KEY in the environment (live LLM call)",
)
def test_live_pipeline_extracts_invoice_number():
    from invoice_agent.config import get_settings
    from invoice_agent.interface.cli import build_use_case

    email_path = Path("data/Email.json")
    if not email_path.is_file():
        pytest.skip("data/Email.json is not present")

    use_case = build_use_case(get_settings(), str(email_path))
    notification = use_case.execute()

    assert notification.summary
    assert notification.payload.invoice_number  # extracted (incl. image-only fields)
