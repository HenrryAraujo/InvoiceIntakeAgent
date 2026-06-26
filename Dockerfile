# Invoice-intake agent image. Both services in docker-compose.yml use this image:
# the `app` service runs FastAPI; the `mlflow` service runs the tracking server.
#
# uv is copied from a pinned uv image into the official slim Python base (pinned).

FROM python:3.11-slim-bookworm

# Pinned uv binary (matches the host toolchain)
COPY --from=ghcr.io/astral-sh/uv:0.8.22 /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# 1) Install dependencies only (cached layer, no project build yet)
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev

# 2) Copy the project and install it
COPY README.md LICENSE main.py ./
COPY src ./src
RUN uv sync --frozen --no-dev

# Non-root user + writable dirs that named volumes inherit ownership from
RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /app/output /mlflow/artifacts \
    && chown -R appuser:appuser /app /mlflow

USER appuser
ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=5 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/health').status==200 else 1)"

CMD ["uvicorn", "invoice_agent.interface.api:app", "--host", "0.0.0.0", "--port", "8000"]
