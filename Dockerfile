FROM python:3.13-alpine

ARG VCS_REF=unknown
ARG IMAGE_VERSION=dev

LABEL org.opencontainers.image.title="TypeScale" \
    org.opencontainers.image.description="Distributed multiplayer typing race" \
    org.opencontainers.image.source="https://github.com/KoushikPraneeth/typescale-app" \
    org.opencontainers.image.revision="$VCS_REF" \
    org.opencontainers.image.version="$IMAGE_VERSION"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt \
    && python -m pip uninstall -y pip setuptools wheel \
    && addgroup -g 10001 -S app \
    && adduser -u 10001 -S -D -H -G app app

COPY --chown=app:app app ./app
COPY --chown=app:app frontend ./frontend

USER app

EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=3s \
    --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2)" || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]