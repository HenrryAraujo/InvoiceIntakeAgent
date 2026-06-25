"""Single-command entrypoint for the invoice-intake agent.

Phase 0 provides configuration bootstrap only; the full processing pipeline
(agent + tools) is wired in Phase 1. See the implementation manifest, section 14.
"""

from __future__ import annotations

from invoice_agent.interface.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
