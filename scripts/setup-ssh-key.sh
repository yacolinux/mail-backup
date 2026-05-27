#!/bin/bash
# =============================================================================
# Configuración de clave SSH para rsync remoto
# =============================================================================
# Ejecutar UNA VEZ antes de habilitar el backup remoto.
# Genera un par de claves SSH y muestra instrucciones para el servidor remoto.

set -e

SSH_DIR="./config/ssh"
KEY_FILE="${SSH_DIR}/id_rsa"
KEY_COMMENT="zimbra-backup-$(hostname)-$(date +%Y%m%d)"

mkdir -p "${SSH_DIR}"
chmod 700 "${SSH_DIR}"

if [ -f "${KEY_FILE}" ]; then
    echo "⚠  Ya existe una clave SSH en ${KEY_FILE}"
    read -p "¿Regenerar? (s/N): " confirm
    [[ "${confirm}" != "s" && "${confirm}" != "S" ]] && exit 0
fi

echo ""
echo "Generando clave SSH RSA 4096 bits..."
ssh-keygen -t rsa -b 4096 -f "${KEY_FILE}" -N "" -C "${KEY_COMMENT}"
chmod 600 "${KEY_FILE}"
chmod 644 "${KEY_FILE}.pub"

echo ""
echo "✓ Clave generada en:"
echo "  Privada: ${KEY_FILE}"
echo "  Pública: ${KEY_FILE}.pub"
echo ""
echo "════════════════════════════════════════════════════"
echo "INSTRUCCIONES PARA EL SERVIDOR REMOTO:"
echo "════════════════════════════════════════════════════"
echo ""
echo "1. Agregar esta clave pública al servidor ${RSYNC_HOST:-backup.example.com}:"
echo ""
cat "${KEY_FILE}.pub"
echo ""
echo "2. En el servidor remoto, ejecutar:"
echo "   sudo mkdir -p /backups/zimbra"
echo "   sudo chown ${RSYNC_USER:-backupuser}:${RSYNC_USER:-backupuser} /backups/zimbra"
echo ""
echo "3. Agregar la clave al authorized_keys del usuario ${RSYNC_USER:-backupuser}:"
echo "   cat >> ~/.ssh/authorized_keys << 'EOF'"
cat "${KEY_FILE}.pub"
echo "   EOF"
echo ""
echo "4. Habilitar rsync en config/backup.conf:"
echo "   [remote]"
echo "   enabled = true"
echo "   host = ${RSYNC_HOST:-backup.example.com}"
echo "   user = ${RSYNC_USER:-backupuser}"
echo "   path = /backups/zimbra"
echo ""
echo "5. Reiniciar el servicio:"
echo "   docker compose restart backup-service"
echo ""
