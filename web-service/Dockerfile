FROM python:3.12-slim-bookworm

LABEL maintainer="zimbra-backup-system"
LABEL description="Zimbra Backup Web Interface - Fase 2"

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    libldap2-dev \
    libsasl2-dev \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd -r webuser && useradd -r -g webuser -m -d /home/webuser webuser

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/
COPY entrypoint.sh ./
RUN chmod +x entrypoint.sh

RUN mkdir -p /data/logs && chown -R webuser:webuser /data /app

EXPOSE 8080

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=UTC

USER webuser

ENTRYPOINT ["./entrypoint.sh"]
