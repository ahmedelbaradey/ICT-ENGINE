# ICT-Kronos worker image.
#
# Deliberately LEAN: only the core runtime deps. PyTorch/Kronos ([kronos]) and the
# ML stack ([ml]) are NOT installed here — they are opt-in extras layered by a
# separate image when Phase 4/5 actually needs them (CLAUDE.md rule 9). Keeping the
# default image small keeps CI, local dev and deploys fast.

FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Dependency layer first so source edits do not invalidate the pip cache.
COPY pyproject.toml README.md ./
COPY ict_kronos/__init__.py ./ict_kronos/
RUN pip install --upgrade pip && pip install .

COPY ict_kronos/ ./ict_kronos/

# Non-root: the worker never needs to write outside the mounted data volume.
RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /data \
    && chown -R appuser:appuser /app /data
USER appuser

ENV DATA_ROOT=/data \
    MARKET_DATA_BACKEND=fixture \
    KRONOS_BACKEND=mock \
    LLM_BACKEND=mock \
    LOG_LEVEL=INFO

# Phase 1 ships the CLI; the long-running poll lanes arrive with the outbox (Phase 3+).
ENTRYPOINT ["python", "-m", "ict_kronos.cli"]
CMD ["--help"]
