FROM python:3.11-slim AS builder

WORKDIR /app

# Install uv for fast dependency management (pinned for reproducible builds)
RUN pip install --no-cache-dir "uv==0.9.0"

# Copy dependency files first for better layer caching
COPY pyproject.toml uv.lock ./

# Install dependencies using uv
RUN uv sync --frozen --no-dev --no-install-project

COPY . .

# Compile only our app code (skip .venv) and drop the .py sources
RUN python -m compileall -b -q main.py api \
    && find main.py api -name "*.py" -delete

FROM python:3.11-slim

WORKDIR /app

# Copy installed dependencies and app from builder
COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/main.pyc /app/main.pyc
COPY --from=builder /app/api /app/api

# Set PATH to use venv binaries
ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
