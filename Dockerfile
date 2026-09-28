# syntax=docker/dockerfile:1.7
# Imagen del backend (FastAPI) y del job de ingesta (mismo código, distinto comando).

FROM python:3.12-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:0.11.1 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock .python-version ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project
COPY app ./app
COPY ingestor ./ingestor

FROM python:3.12-slim AS runtime
RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /data && chown appuser /data   # SQLite local (volumen app_data)
WORKDIR /app
COPY --from=builder --chown=appuser /app /app
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1
USER appuser
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
