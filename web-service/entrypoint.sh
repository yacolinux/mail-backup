#!/bin/bash
# =============================================================================
# Zimbra Backup Web Service - Entrypoint
# =============================================================================
set -e

echo "[entrypoint] Zimbra Backup Web Interface v1.0"
echo "[entrypoint] BACKUP_API_URL: ${BACKUP_API_URL:-http://backup-service:8001}"

mkdir -p /data/logs

# Verificar conectividad con el backup-service
MAX_RETRIES=10
COUNT=0
echo "[entrypoint] Esperando que el backup-service esté disponible..."
until curl -sf "${BACKUP_API_URL:-http://backup-service:8001}/api/v1/health" > /dev/null 2>&1; do
    COUNT=$((COUNT + 1))
    if [ $COUNT -ge $MAX_RETRIES ]; then
        echo "[entrypoint] WARNING: backup-service no disponible, iniciando de todas formas..."
        break
    fi
    echo "[entrypoint] Esperando backup-service... ($COUNT/$MAX_RETRIES)"
    sleep 5
done

echo "[entrypoint] Iniciando servidor web en 0.0.0.0:8080..."
exec gunicorn \
    --bind 0.0.0.0:8080 \
    --workers 2 \
    --worker-class sync \
    --timeout 60 \
    --access-logfile /data/logs/access.log \
    --error-logfile /data/logs/error.log \
    --log-level info \
    "src.app:create_app()"
