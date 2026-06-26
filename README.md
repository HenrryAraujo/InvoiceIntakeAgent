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
2. **`send_notification`** — writes the summary + JSON payload and returns a confirmation.

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
Place them in the `data/` folder before running:

```
data/
├── Email.json     # inbound email (Microsoft Graph message envelope)
└── Invoice.pdf    # the PDF attachment referenced by the email
```

The attachment is resolved by the name referenced in `Email.json` and must live in `data/`.

## Run

```bash
uv run python main.py --email ./data/Email.json
```

The command prints the Customer Service summary, logs the agent tool sequence
(`extract_invoice_data -> send_notification`), and writes the two output files below.

## HTTP API

The API is served by the same `ProcessInvoiceUseCase` as the CLI:

```bash
uv run uvicorn invoice_agent.interface.api:app --reload
```

| Method | Path | Description |
| --- | --- | --- |
| `GET` | `/health` | Liveness probe — returns `{"status": "ok"}`. |
| `POST` | `/process-invoice` | Runs the agent and returns `{ "summary", "payload" }`; also writes the output files. |

Process the default mock inbound (`data/Email.json` + `data/Invoice.pdf`):

```bash
curl -X POST http://127.0.0.1:8000/process-invoice
```

Override with your own email JSON + PDF (multipart — provide both):

```bash
curl -X POST http://127.0.0.1:8000/process-invoice \
  -F "email=@./data/Email.json;type=application/json" \
  -F "pdf=@./data/Invoice.pdf;type=application/pdf"
```

## Output

| File | Content |
| --- | --- |
| `output/outbound_email.txt` | Human-readable, sectioned Customer Service summary |
| `output/outbound_email.json` | Structured `InvoiceData` payload for downstream processing |

## Observability

Each run logs **privacy-safe** telemetry to MLflow (SQLite-backed by default): only metrics,
safe params, and content **hashes** — no raw email/PDF text, prompts, or responses, and no
MLflow autologging.

Per-run metrics include `prompt_tokens` / `completion_tokens` (agent + vision aggregated),
`estimated_cost_usd`, `latency_ms`, per-tool latencies, `field_coverage_pct`, and
`validation_passed`; the tool sequence and content hashes are recorded as tags.

View the runs in the MLflow UI:

```bash
uv run mlflow ui --backend-store-uri sqlite:///mlflow.db
# then open http://127.0.0.1:5000
```

Tracing is on by default (`ENABLE_TRACING=false` to disable). An optional `gpt-5-nano`
faithfulness judge is available behind `ENABLE_JUDGE=true` (off by default to save credits).

## Configuration

All settings are environment-driven (loaded from `.env`); defaults shown.

| Variable | Default | Purpose |
| --- | --- | --- |
| `OPENAI_API_KEY` | _(required)_ | Key for the live extraction / agent calls |
| `INPUT_DIR` | `data` | Folder holding `Email.json` and the PDF |
| `OUTPUT_DIR` | `output` | Where the notification files are written |
| `EXTRACTOR_MODEL` | `gpt-5-mini` | Vision extraction model |
| `AGENT_MODEL` | `gpt-5-mini` | Agent orchestration model |
| `JUDGE_MODEL` | `gpt-5-nano` | Optional LLM judge (disabled by default) |
| `RENDER_DPI` | `150` | PDF page render resolution (cost cap) |
| `MAX_PAGES` | `4` | Max PDF pages sent to the vision model (cost cap) |
| `MAX_TURNS` | `4` | Max agent turns |
| `ENABLE_TRACING` | `true` | Log per-run metrics to MLflow |
| `ENABLE_JUDGE` | `false` | Optional `gpt-5-nano` faithfulness score |
| `MLFLOW_TRACKING_URI` | `sqlite:///mlflow.db` | MLflow backing store |

Only `gpt-5-mini` and `gpt-5-nano` are accepted for any model setting — any other value
fails fast at startup. If a PDF has more than `MAX_PAGES` pages and you want every page
analyzed, raise `MAX_PAGES` (this increases vision cost).

## Design notes

- **Cost-conscious:** one vision call per run, capped DPI and page count, no retry loops.
- **Robust:** unreadable PDFs, missing attachments, and partial extractions return clear
  errors or warnings instead of crashing.
- **Private:** `.env` and raw email/PDF content are never committed or written to logs.

## Roadmap

A pytest suite and a Docker Compose stack are delivered as subsequent increments.