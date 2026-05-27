#!/bin/bash
# =============================================================================
# Zimbra Backup Service - Entrypoint
# =============================================================================
set -e

COMMAND="${1:-daemon}"

echo "[entrypoint] Zimbra Backup System v1.0"
echo "[entrypoint] Comando: ${COMMAND}"

# Crear directorios si no existen (por si acaso el volumen es nuevo)
mkdir -p /data/backups/accounts /data/db /data/logs /data/git

# Configurar permisos de SSH key si existe
if [ -f /config/ssh/id_rsa ]; then
    chmod 600 /config/ssh/id_rsa
    echo "[entrypoint] SSH key encontrada en /config/ssh/id_rsa"
fi

# Marcar /data/git como directorio seguro para git
git config --global --add safe.directory /data/git 2>/dev/null || true

# Inicializar sistema (crea BD, repositorio git, etc.)
python -m src.cli init 2>/dev/null || true

case "${COMMAND}" in
    daemon)
        echo "[entrypoint] Iniciando daemon de backup..."
        # Iniciar API REST en background (proceso bloqueante independiente)
        python -c "
from src.config import load_config
from src.db import Database
from src.api import run_api
import logging
logging.basicConfig(level=logging.WARNING)
config = load_config()
db = Database(config.db_path)
print('[api] REST API iniciando en :8001')
run_api(config, db)
" &
        # Iniciar daemon principal
        exec python -m src.cli daemon
        ;;

    api-only)
        echo "[entrypoint] Iniciando solo API REST..."
        exec python -c "
from src.config import load_config
from src.db import Database
from src.api import run_api
config = load_config()
db = Database(config.db_path)
run_api(config, db)
"
        ;;

    cli|zbackup)
        shift
        exec python -m src.cli "$@"
        ;;

    *)
        # Pasar al CLI directamente (ej: "backup", "status", etc.)
        exec python -m src.cli "${COMMAND}" "${@:2}"
        ;;
esac
