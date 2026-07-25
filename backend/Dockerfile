FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DEFAULT_TIMEOUT=120 \
    PIP_RETRIES=10 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY requirements.txt requirements-dev.txt requirements.lock.txt ./
RUN --mount=type=cache,target=/root/.cache/pip \
    for attempt in 1 2 3 4 5; do \
        pip install --retries 10 --timeout 120 -r requirements.lock.txt && exit 0; \
        if [ "$attempt" -eq 5 ]; then exit 1; fi; \
        echo "pip download interrupted; retrying dependency install ($attempt/5)"; \
    done

COPY alembic.ini ./
COPY pyproject.toml ./
COPY migrations ./migrations
COPY app ./app
COPY scripts ./scripts
COPY tests ./tests

# Bake the pinned, checksum-verified ONNX models into the image so the runtime is
# self-contained: no network access and no manual model provisioning at deploy time.
# Skippable for offline image builds via `--build-arg BAKE_MODELS=0` (then mount ./models).
ARG BAKE_MODELS=1
RUN if [ "$BAKE_MODELS" = "1" ]; then \
        python scripts/provision_models.py --model-root /app/models; \
    fi

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
