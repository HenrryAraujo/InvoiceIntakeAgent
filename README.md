# Invoice-Intake Agent

An agent built on the **OpenAI Agents SDK** that ingests an inbound email with a PDF
invoice attachment, extracts structured invoice data — **including fields that exist only
inside an embedded image** (such as the invoice number) — and produces a Customer Service
notification: a human-readable summary plus a structured JSON payload.

## How it works

The project uses a hexagonal (ports & adapters) architecture: a single
`ProcessInvoiceUseCase` depends on abstract ports, and all I/O lives in injected adapters,
so the same code path can back both the CLI and the HTTP API.

The agent calls exactly two tools, in order:

1. **`extract_invoice_data`** — PyMuPDF extracts the PDF text and renders pages to images,
   then a single `gpt-5-mini` vision call returns the structured fields (reconciling text
   with values that appear only inside images).
2. **`send_notification`** — applies the deterministic approval policy (see
   [Human-in-the-loop approval](#human-in-the-loop-approval)), then writes the decision card
   + summary and the structured JSON payload, and returns a confirmation.

The approval decision is a **pure domain service** invoked inside `send_notification`, so the
agent still calls exactly two tools — a financial gate must never depend on an LLM.

## Requirements

- [uv](https://docs.astral.sh/uv/) (manages Python 3.11 and all dependencies)
- An OpenAI API key with access to `gpt-5-mini` / `gpt-5-nano`

## Setup

```bash
# 1) Install dependencies (creates .venv and resolves from uv.lock)
uv sync

# 2) Create your .env from the template and add your key
cp .env.example .env            # Windows PowerShell: Copy-Item .env.example .env
# then edit .env and set: OPENAI_API_KEY=sk-...
```

### Input data

The sample email and PDF are **not committed** (raw content stays out of version control).
Place them in the `input_data/` folder before running:

```
input_data/
├── Email.json     # inbound email (Microsoft Graph message envelope)
└── Invoice.pdf    # the PDF attachment referenced by the email
```

The attachment is resolved by the name referenced in `Email.json` and must live in `input_data/`.

## Run

```bash
uv run python main.py --email ./input_data/Email.json

# Choose the acting approver persona (default: ACTIVE_PERSONA from .env):
uv run python main.py --persona rep          # low authority  -> likely "approval required"
uv run python main.py --persona supervisor   # high authority -> likely "auto-approved"
```

The command prints the approval decision banner and the Customer Service summary, logs each
step (load -> extract -> vision call -> decision -> write), and writes the two output files
below.

## HTTP API

The API is served by the same `ProcessInvoiceUseCase` as the CLI:

```bash
uv run uvicorn invoice_agent.interface.api:app --reload
```

| Method | Path | Description |
| --- | --- | --- |
| `GET` | `/health` | Liveness probe — returns `{"status": "ok"}`. |
| `POST` | `/process-invoice` | Runs the agent and returns `{ "summary", "payload", "decision" }`; also writes the output files. Accepts an optional `?persona=rep|supervisor`. |

Process the default mock inbound (`input_data/Email.json` + `input_data/Invoice.pdf`):

```bash
curl -X POST "http://127.0.0.1:8000/process-invoice?persona=supervisor"
```

Override with your own email JSON + PDF (multipart — provide both):

```bash
curl -X POST http://127.0.0.1:8000/process-invoice \
  -F "email=@./input_data/Email.json;type=application/json" \
  -F "pdf=@./input_data/Invoice.pdf;type=application/pdf"
```

## Output

| File | Content |
| --- | --- |
| `output_data/outbound_email.txt` | Approval decision card + human-readable, sectioned Customer Service summary |
| `output_data/outbound_email.json` | Full notification (`summary`, `payload`, and `decision`) for downstream processing |

## Human-in-the-loop approval

After extraction, a **deterministic** Delegation-of-Authority policy decides how the invoice
is routed — no LLM is involved, so the financial gate stays auditable and free of model cost.
The acting **persona** carries an approval limit; the invoice total is compared against it:

| Persona | Default limit (CAD) | Outcome on the sample invoice |
| --- | --- | --- |
| `rep` (Customer Service Representative) | `10,000` | total exceeds limit → **APPROVAL REQUIRED** (escalated to the configured contact) |
| `supervisor` (Customer Service Supervisor) | `150,000` | total within limit → **AUTO-APPROVED** (routed for processing) |

Pick the persona per run with `--persona {rep|supervisor}` (CLI) or `?persona=` (API); the
default is `ACTIVE_PERSONA` from `.env`. Limits, titles, currency, the escalation contact,
and the duplicate-hold policy are all configurable (see [Configuration](#configuration)).

The decision also runs integrity checks (schema validation, total present, tax reconciliation,
duplicate flag). Any integrity failure routes the invoice to **ON HOLD** for human review. The
outcome is written as a decision card atop `outbound_email.txt` and as a `decision` object in
`outbound_email.json`, and is recorded on the MLflow run (`persona`, `decision_status` tags).

## Logging

The CLI and the API share one logger configured from the environment, emitting step progress
for each stage (load email → render → vision call → approval decision → write) with
**metadata only** — filenames, counts, models, and the decision status, never raw email/PDF
content.

| `LOG_LEVEL` | Use it for |
| --- | --- |
| `DEBUG` (default) | Full local-development detail |
| `INFO` | Minimal step progress |
| `WARNING` / `ERROR` | Quiet — problems only |

Set `LOG_FORMAT=json` for structured logs suited to production telemetry ingestion
(`LOG_FORMAT=plain` is the default human-readable format).

## Observability

Each run logs **privacy-safe** telemetry to MLflow (SQLite-backed by default): only metrics,
safe params, and content **hashes** — no raw email/PDF text, prompts, or responses, and no
MLflow autologging.

Per-run metrics include `prompt_tokens` / `completion_tokens` (agent + vision aggregated),
`estimated_cost_usd`, `latency_ms`, per-tool latencies, `field_coverage_pct`, and
`validation_passed`; the tool sequence, acting `persona`, `decision_status`, and content
hashes are recorded as tags.

View the runs in the MLflow UI:

```bash
uv run mlflow ui --backend-store-uri sqlite:///mlflow.db
# then open http://127.0.0.1:5000
```

Tracing is on by default (`ENABLE_TRACING=false` to disable). An optional `gpt-5-nano`
faithfulness judge is available behind `ENABLE_JUDGE=true` (off by default to save credits).

## Docker

Run the full stack — the FastAPI app plus an MLflow tracking server (SQLite-backed):

```bash
docker compose up -d --build
```

| Service | URL | Notes |
| --- | --- | --- |
| `app` (FastAPI) | http://localhost:8000 | `GET /health`, `POST /process-invoice` |
| `mlflow` (UI) | http://localhost:5000 | per-request run metrics |

The app reads `OPENAI_API_KEY` from `.env`, mounts `./input_data` read-only, and persists
output and MLflow data in named volumes. Check health, then stop:

```bash
curl http://localhost:8000/health
docker compose down          # add -v to also remove the named volumes
```

## Configuration

All settings are environment-driven (loaded from `.env`); defaults shown.

| Variable | Default | Purpose |
| --- | --- | --- |
| `OPENAI_API_KEY` | _(required)_ | Key for the live extraction / agent calls |
| `INPUT_DIR` | `input_data` | Folder holding `Email.json` and the PDF |
| `OUTPUT_DIR` | `output_data` | Where the notification files are written |
| `EXTRACTOR_MODEL` | `gpt-5-mini` | Vision extraction model |
| `AGENT_MODEL` | `gpt-5-mini` | Agent orchestration model |
| `JUDGE_MODEL` | `gpt-5-nano` | Optional LLM judge (disabled by default) |
| `RENDER_DPI` | `150` | PDF page render resolution (cost cap) |
| `MAX_PAGES` | `4` | Max PDF pages sent to the vision model (cost cap) |
| `MAX_TURNS` | `4` | Max agent turns |
| `LOG_LEVEL` | `DEBUG` | Logging verbosity (`DEBUG`/`INFO`/`WARNING`/`ERROR`/`CRITICAL`) |
| `LOG_FORMAT` | `plain` | Log format: `plain` or `json` (structured telemetry) |
| `ENABLE_TRACING` | `true` | Log per-run metrics to MLflow |
| `ENABLE_JUDGE` | `false` | Optional `gpt-5-nano` faithfulness score |
| `MLFLOW_TRACKING_URI` | `sqlite:///mlflow.db` | MLflow backing store |
| `ACTIVE_PERSONA` | `rep` | Default approver persona (`rep` or `supervisor`) |
| `PERSONA_REP_LIMIT` | `10000` | Representative approval limit |
| `PERSONA_SUPERVISOR_LIMIT` | `150000` | Supervisor approval limit |
| `APPROVAL_CURRENCY` | `CAD` | Currency label for the approval decision |
| `ESCALATION_CONTACT` | `Finance Manager` | Who an over-limit invoice is escalated to |
| `HOLD_ON_DUPLICATE` | `false` | If `true`, a duplicate flag forces ON HOLD |

Only `gpt-5-mini` and `gpt-5-nano` are accepted for any model setting — any other value
fails fast at startup. If a PDF has more than `MAX_PAGES` pages and you want every page
analyzed, raise `MAX_PAGES` (this increases vision cost).

## Tests

The suite is **offline by default**: it uses synthetic fixtures (a generated PDF and a mock
Microsoft Graph email) and a faked vision client, so it makes no network calls and spends
no credits.

```bash
# Run the full offline suite
uv run pytest

# Verbose, and report the reason for any skipped/deselected tests
uv run pytest -v -ra

# Run a single file, or a single test
uv run pytest tests/test_pdf_extractor.py
uv run pytest tests/test_api.py::test_health_returns_ok
```

It covers every layer: domain models (coercion, `Decimal` integrity, tax/allocation
structure), the inbound-email adapter (Graph parsing + safe attachment resolution), the
notifier, the PDF extractor (mocked vision client), the application use case (fakes), the
FastAPI endpoints (`TestClient` with the use case overridden), and the observability helpers.

### Live (opt-in) test

One test runs the real agent end-to-end. It is **deselected by default** and runs only when
you opt in with `-m live` **and** `OPENAI_API_KEY` is set in your shell environment (a `.env`
file is not loaded for tests, so it stays skipped during a normal run):

```bash
# PowerShell — provide the key in the environment, then select the live marker
$env:OPENAI_API_KEY = "sk-..."; uv run pytest -m live
```

## Design notes

- **Cost-conscious:** one vision call per run, capped DPI and page count, no retry loops.
- **Auditable approvals:** the Delegation-of-Authority decision is deterministic (no LLM), so
  routing is reproducible and explainable, and the agent keeps exactly two tools.
- **Robust:** unreadable PDFs, missing attachments, and partial extractions return clear
  errors or warnings instead of crashing.
- **Private:** `.env` and raw email/PDF content are never committed or written to logs.