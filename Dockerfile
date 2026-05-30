FROM python:3.11-slim AS builder

WORKDIR /app

# Install uv for fast dependency management (pinned for reproducible builds)
# and unzip for extracting the NINJAL UniDic archive (Deflate64-compressed).
RUN pip install --no-cache-dir "uv==0.9.0" \
    && apt-get update \
    && apt-get install -y --no-install-recommends unzip \
    && rm -rf /var/lib/apt/lists/*

# Copy dependency files first for better layer caching
COPY pyproject.toml uv.lock ./

# Install dependencies using uv
RUN uv sync --frozen --no-dev --no-install-project

# Download and install the UniDic dictionary into the venv so the image is
# self-contained — the `unidic` pip package ships the loader but not the
# dicdir, and fugashi.Tagger() fails at runtime without it.
# Uses scripts/download_unidic.sh so local dev and Docker share the same
# download logic.  Kept as its own layer (before COPY . .) so app-code
# edits don't bust the download cache; only re-runs when the script or
# uv.lock changes.
COPY scripts/download_unidic.sh scripts/
RUN VIRTUAL_ENV=/app/.venv bash scripts/download_unidic.sh

COPY . .

# Compile only our app code (skip .venv) and drop the .py sources
RUN python -m compileall -b -q main.py api \
    && find main.py api -name "*.py" -delete

FROM python:3.11-slim

# Least privilege: run as an unprivileged user, not root. The app binds
# port 8000 (>1024) so no root is needed. App files stay root-owned and
# read-only to this user — the process can't modify its own code or deps.
RUN useradd --system --no-create-home --uid 10001 appuser

WORKDIR /app

# Copy installed dependencies and app from builder
COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/main.pyc /app/main.pyc
COPY --from=builder /app/api /app/api

# Set PATH to use venv binaries
ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1

EXPOSE 8000

USER appuser

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
