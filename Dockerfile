# syntax=docker/dockerfile:1
FROM python:3.12-slim

ARG VCS_REF="unknown"
ARG VERSION="dev"
ARG SOURCE_URL="https://github.com/jemaiihamza0-hash/BoursaMind-RAG"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DEFAULT_TIMEOUT=120 \
    PIP_RETRIES=5

WORKDIR /app

COPY pyproject.toml requirements.lock ./
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --requirement requirements.lock

COPY app ./app
COPY alembic.ini ./
COPY alembic ./alembic
COPY migrations ./migrations

LABEL org.opencontainers.image.title="BoursaMind RAG" \
      org.opencontainers.image.source=$SOURCE_URL \
      org.opencontainers.image.revision=$VCS_REF \
      org.opencontainers.image.version=$VERSION

RUN useradd --create-home --uid 1001 appuser \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8001
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8001"]
