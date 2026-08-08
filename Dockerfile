FROM python:3.12.11-slim-bookworm AS data-builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /build

COPY requirements-build.lock ./
RUN python -m pip install --disable-pip-version-check --requirement requirements-build.lock \
    && python -m pip check

# The organizer sources and ETL exist only in this build stage. The final
# runtime image receives the verified serving database, not the raw workbooks.
COPY . .
RUN python scripts/verify_sources.py \
    && python scripts/build_data.py --no-parquet \
    && python scripts/scan_runtime_compliance.py


FROM python:3.12.11-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    APP_ENV=production \
    PLANNER_MODE=hcx \
    HCX_MODEL_ID=HCX-007 \
    MIRAE_DATABASE_PATH=/app/data/serving/mirae_agent.duckdb

WORKDIR /app

RUN groupadd --system agent && useradd --system --gid agent --home-dir /app agent

COPY requirements-runtime.lock ./
RUN python -m pip install --disable-pip-version-check --requirement requirements-runtime.lock \
    && python -m pip check

COPY --chown=agent:agent app ./app
COPY --chown=agent:agent registry ./registry
COPY --chown=agent:agent web ./web
COPY --from=data-builder --chown=agent:agent \
    /build/data/serving/mirae_agent.duckdb \
    /app/data/serving/mirae_agent.duckdb

USER agent
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=3s --start-period=30s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health/ready', timeout=2)" || exit 1

# Access logs are disabled because GET URLs contain private evaluation questions.
# --limit-concurrency caps in-flight connections independent of the app's own
# DB/HCX semaphores (DB_MAX_CONCURRENCY maxes at 32) -- a backstop against a
# retry storm or harness bug burning NCP credit, which the organizer does not
# reimburse.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", "--no-access-log", "--proxy-headers", "--limit-concurrency", "64"]
