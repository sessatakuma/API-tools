FROM python:3.11-slim AS builder

WORKDIR /app

# Install uv for fast dependency management
RUN pip install --no-cache-dir uv

# Copy dependency files first for better layer caching
COPY pyproject.toml uv.lock ./

# Install dependencies using uv
RUN uv sync --frozen --no-dev --no-install-project

COPY . .

RUN python -m compileall -b /app \
    && python - <<'PY'
from pathlib import Path

for path in Path("/app").rglob("*.py"):
    path.unlink()
PY

FROM python:3.11-slim

WORKDIR /app

# Copy installed dependencies and app from builder
COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/main.pyc /app/main.pyc
COPY --from=builder /app/api /app/api
COPY --from=builder /app/config /app/config

# Set PATH to use venv binaries
ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
