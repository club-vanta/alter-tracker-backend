# ── Stage 1: install dependencies ────────────────────────────────────────────
FROM python:3.13-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv

WORKDIR /app

# Copy lockfiles first — this layer is cached unless dependencies change
COPY pyproject.toml uv.lock ./

# Install production dependencies into a virtual environment
# --no-install-project skips installing the project itself (we copy it next)
RUN uv sync --frozen --no-dev --no-install-project

# Copy the rest of the project and do a full sync
COPY app/ ./app/
COPY alembic/ ./alembic/
COPY alembic.ini ./
RUN uv sync --frozen --no-dev


# ── Stage 2: final image ──────────────────────────────────────────────────────
FROM python:3.13-slim AS final

WORKDIR /app

# Don't write .pyc files, don't buffer stdout/stderr
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

# Copy only the virtual environment and application code from the builder
COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/app ./app
COPY --from=builder /app/alembic ./alembic
COPY --from=builder /app/alembic.ini .

# Run as a non-root user
RUN useradd --no-create-home --shell /bin/false appuser \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
