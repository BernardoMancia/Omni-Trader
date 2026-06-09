# ---- Build stage ----
FROM python:3.11-slim AS builder

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY services/requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# ---- Runtime stage ----
FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 curl \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd -r omni && useradd -r -g omni -d /app -s /sbin/nologin omni

COPY --from=builder /install /usr/local
COPY . .

RUN mkdir -p /app/models && chown -R omni:omni /app

USER omni

ENV PYTHONUNBUFFERED=1
ENV SERVICE_NAME=notifier

CMD ["sh", "-c", "python -m services.${SERVICE_NAME}.main"]
