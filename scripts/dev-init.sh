#!/bin/bash
# =============================================================================
# Zimbra Backup System - Inicialización para Desarrollo
# =============================================================================
# Este script configura automáticamente un entorno de desarrollo/pruebas
# con maildir local y autenticación demo.
#
# Uso: ./scripts/dev-init.sh
# =============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
WHITE='\033[1;37m'
NC='\033[0m' # No Color

echo -e "${GREEN}╔════════════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║   Zimbra Backup System - Setup de Desarrollo          ║${NC}"
echo -e "${GREEN}╚════════════════════════════════════════════════════════╝${NC}"
echo ""

cd "$PROJECT_DIR"

# Paso 1: Copiar archivos de configuración de desarrollo
echo -e "${YELLOW}[1/5] Configurando archivos de entorno...${NC}"
if [ -f ".env.dev" ]; then
    echo -e "  ✓ .env.dev ya existe"
else
    echo -e "  ✗ .env.dev no existe - ejecuta este script desde el directorio del proyecto"
    exit 1
fi

# Paso 2: Generar emails de prueba si no existen
echo -e "${YELLOW}[2/5] Verificando emails de prueba...${NC}"
if [ -d "dev-maildir/example.com/admin/Maildir/cur" ] && \
   [ "$(ls -A dev-maildir/example.com/admin/Maildir/cur 2>/dev/null)" ]; then
    echo -e "  ✓ Emails de prueba ya existen"
else
    echo -e "  → Generando emails de prueba..."
    python3 scripts/generate_test_emails.py
fi

# Paso 3: Verificar clave SSH (opcional para desarrollo)
echo -e "${YELLOW}[3/5] Verificando clave SSH...${NC}"
if [ -f "config/ssh/id_rsa" ]; then
    echo -e "  ✓ Clave SSH ya existe"
else
    echo -e "  → Generando clave SSH para desarrollo..."
    mkdir -p config/ssh
    ssh-keygen -t rsa -b 2048 -f config/ssh/id_rsa -N "" -q
    echo -e "  ✓ Clave SSH generada (solo para desarrollo)"
fi

# Paso 4: Verificar Docker
echo -e "${YELLOW}[4/5] Verificando Docker...${NC}"
if command -v docker &> /dev/null && docker compose version &> /dev/null; then
    echo -e "  ✓ Docker y Docker Compose disponibles"
    docker compose version --short
else
    echo -e "  ${RED}✗ Docker o Docker Compose no están instalados${NC}"
    exit 1
fi

# Paso 5: Iniciar servicios
echo -e "${YELLOW}[5/5] Iniciando servicios...${NC}"
echo -e "  → Construyendo contenedores..."
docker compose -f docker-compose.dev.yml up -d --build

# Esperar a que el servicio esté listo
echo -e "  → Esperando que el backup-service esté listo..."
sleep 5

# Verificar salud
echo -e ""
echo -e "${GREEN}✓ ¡Entorno de desarrollo iniciado!${NC}"
echo ""
echo -e "${YELLOW}╔══════════════════════════════════════════════════════╗${NC}"
echo -e "${YELLOW}║              INFORMACIÓN DE ACCESO                   ║${NC}"
echo -e "${YELLOW}╠══════════════════════════════════════════════════════╣${NC}"
echo -e "${YELLOW}║${NC}  Web Interface:  ${GREEN}http://localhost:8080${NC}                  ${YELLOW}║${NC}"
echo -e "${YELLOW}║${NC}                                                      ${YELLOW}║${NC}"
echo -e "${YELLOW}║${NC}  ${GREEN}Usuarios Demo:${NC}                                       ${YELLOW}║${NC}"
echo -e "${YELLOW}║${NC}    • Admin: ${WHITE}admin@example.com / admin123${NC}            ${YELLOW}║${NC}"
echo -e "${YELLOW}║${NC}    • User1: ${WHITE}user1@example.com / user123${NC}             ${YELLOW}║${NC}"
echo -e "${YELLOW}║${NC}    • User2: ${WHITE}user2@example.com / user123${NC}             ${YELLOW}║${NC}"
echo -e "${YELLOW}╠══════════════════════════════════════════════════════╣${NC}"
echo -e "${YELLOW}║${NC}  ${RED}⚠  SOLO PARA DESARROLLO - NO USAR EN PRODUCCIÓN${NC}  ${YELLOW}║${NC}"
echo -e "${YELLOW}╚══════════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "${YELLOW}Comandos útiles:${NC}"
echo -e "  ${GREEN}make dev-status${NC}     - Ver estado del backup"
echo -e "  ${GREEN}make dev-backup${NC}     - Ejecutar backup manual"
echo -e "  ${GREEN}make dev-logs${NC}       - Ver logs en tiempo real"
echo -e "  ${GREEN}make dev-down${NC}       - Detener servicios"
echo ""
