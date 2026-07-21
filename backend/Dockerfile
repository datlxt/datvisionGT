FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt requirements-dev.txt requirements.lock.txt ./
RUN pip install --upgrade pip && pip install -r requirements.lock.txt

COPY alembic.ini ./
COPY pyproject.toml ./
COPY migrations ./migrations
COPY app ./app
COPY scripts ./scripts
COPY tests ./tests

RUN mkdir -p /app/storage/uploads /app/storage/jobs /app/storage/exports /app/models \
    && useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app

EXPOSE 8000

FROM base AS runtime
USER appuser
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

FROM base AS test
RUN pip install -r requirements-dev.txt
USER appuser
CMD ["pytest"]
