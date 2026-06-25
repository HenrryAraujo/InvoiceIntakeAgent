"""CLI entrypoint.

Phase 0: validates and prints the loaded configuration (fail-fast model guard).
Phase 1 will add inbound-email loading, agent execution, and output writing.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from pydantic import ValidationError

from invoice_agent.config import Settings, get_settings


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="invoice-agent",
        description="Invoice-intake agent — ingest an email + PDF and notify Customer Service.",
    )
    parser.add_argument(
        "--email",
        default="./data/Email.json",
        help="Path to the inbound email JSON (default: ./data/Email.json).",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point. Returns a process exit code."""
    args = _build_parser().parse_args(argv)

    try:
        settings: Settings = get_settings()
    except ValidationError as exc:
        print("Configuration error — startup aborted:", file=sys.stderr)
        print(exc, file=sys.stderr)
        return 2

    print("invoice-intake-agent: configuration loaded OK")
    print(f"  email argument : {args.email}")
    print(f"  input_dir      : {settings.input_dir}")
    print(f"  output_dir     : {settings.output_dir}")
    print(f"  extractor_model: {settings.extractor_model}")
    print(f"  agent_model    : {settings.agent_model}")
    print(f"  judge_model    : {settings.judge_model}")
    print("Phase 0 skeleton ready — the processing pipeline lands in Phase 1.")
    return 0
