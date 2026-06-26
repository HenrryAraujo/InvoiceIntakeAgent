"""
    Single-command entrypoint for the invoice-intake agent.
"""

from __future__ import annotations

from invoice_agent.interface.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
