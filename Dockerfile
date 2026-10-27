# syntax=docker/dockerfile:1
FROM python:3.12-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.9 /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN uv sync --frozen --no-dev

FROM python:3.12-slim
RUN useradd --create-home --uid 1000 sentinel
WORKDIR /app
COPY --from=build /app/.venv /app/.venv
COPY --chown=sentinel:sentinel src ./src
COPY --chown=sentinel:sentinel config ./config
COPY --chown=sentinel:sentinel skills ./skills
COPY --chown=sentinel:sentinel evals ./evals
COPY --chown=sentinel:sentinel data/sample ./data/sample
COPY --chown=sentinel:sentinel docker-entrypoint.sh ./
RUN mkdir -p data/snapshots data/state runs && chown -R sentinel:sentinel data runs \
    && chmod +x docker-entrypoint.sh
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1 SENTINEL_TODAY=2026-11-02 \
    SENTINEL_DB=/app/data/snapshots/base.duckdb SENTINEL_STATE_DIR=/app/data/state
USER sentinel
EXPOSE 8501 8765
ENTRYPOINT ["./docker-entrypoint.sh"]
CMD ["ui"]
