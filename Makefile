# =============================================================================
# Zimbra Backup System - Makefile
# =============================================================================
.PHONY: help up down logs shell cli status backup prune web-up web-down setup dev-up dev-down

COMPOSE      = docker compose -f docker-compose.yml
COMPOSE_WEB  = docker compose -f docker-compose.yml -f docker-compose.web.yml
COMPOSE_DEV  = docker compose -f docker-compose.dev.yml
CLI          = $(COMPOSE) exec backup-service zbackup
CLI_DEV      = $(COMPOSE_DEV) exec backup-service zbackup
RED          = \033[0;31m
GREEN        = \033[0;32m
YELLOW       = \033[1;33m
NC           = \033[0m

help: ## Mostrar esta ayuda
	@echo ""
	@echo "  $(GREEN)Zimbra Backup System$(NC)"
	@echo ""
	@echo "  $(YELLOW)Producción (modo remoto):$(NC)"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | grep -v dev | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  $(YELLOW)%-20s$(NC) %s\n", $$1, $$2}'
	@echo ""
	@echo "  $(YELLOW)Desarrollo (maildir local):$(NC)"
	@grep -E '^dev-.*?:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  $(YELLOW)%-20s$(NC) %s\n", $$1, $$2}'
	@echo ""

setup: ## Primer uso: copiar .env.example a .env y crear directorios
	@[ -f .env ] || (cp .env.example .env && echo "$(GREEN)✓ .env creado - EDITAR antes de continuar$(NC)")
	@mkdir -p config/ssh dev-maildir data/backups data/db data/logs data/git data/weblogs
	@chmod -R 777 data/
	@echo "$(GREEN)✓ Directorios creados$(NC)"
	@echo "$(YELLOW)➜  Editar .env y config/backup.conf antes de 'make up'$(NC)"

up: ## Iniciar solo el backup-service (rebuild incluido)
	$(COMPOSE) up -d --build
	@echo "$(GREEN)✓ Backup service iniciado$(NC)"
	@echo "$(YELLOW)➜  Logs: make logs$(NC)"
	@echo "$(YELLOW)➜  CLI:  make cli CMD='status'$(NC)"
	@echo "$(YELLOW)➜  Web:  make web-up$(NC)"

up-all: ## Iniciar backup-service + web-service (rebuild de ambos)
	$(COMPOSE) up -d --build
	@echo "$(YELLOW)➜  Esperando que el backup-service esté listo...$(NC)"
	@sleep 5
	$(COMPOSE_WEB) up -d --build web-service
	@echo "$(GREEN)✓ Todos los servicios iniciados$(NC)"
	@echo "$(YELLOW)➜  Web: http://localhost:$${WEB_PORT:-8080}$(NC)"

down: ## Detener todos los servicios
	$(COMPOSE_WEB) down 2>/dev/null || $(COMPOSE) down

logs: ## Ver logs del backup service
	$(COMPOSE) logs -f --tail=100 backup-service

shell: ## Abrir shell en el contenedor de backup
	$(COMPOSE) exec backup-service bash

# --- CLI shortcuts ---
status: ## Mostrar estado del sistema de backup
	$(CLI) status

backup: ## Ejecutar backup inmediatamente
	$(CLI) backup --trigger manual

list-accounts: ## Listar cuentas backupeadas
	$(CLI) list-accounts

list-emails: ## Listar emails de una cuenta (ej: make list-emails ACCOUNT=user@domain.com)
	$(CLI) list-emails $(ACCOUNT)

list-backups: ## Listar snapshots de una cuenta (ej: make list-backups ACCOUNT=user@domain.com)
	$(CLI) list-backups $(ACCOUNT)

delete-email: ## Borrar email definitivamente (ej: make delete-email ID=123)
	$(CLI) delete-email $(ID) --confirm

prune: ## Aplicar política de retención manualmente
	$(CLI) prune

cli: ## Ejecutar comando CLI (ej: make cli CMD='list-accounts --format=table')
	$(CLI) $(CMD)

# --- Fase 2: Web Interface ---
web-up: ## Iniciar interfaz web (Fase 2 - requiere backup-service activo)
	$(COMPOSE_WEB) up -d --build web-service
	@echo "$(GREEN)✓ Web interface iniciada en http://localhost:$${WEB_PORT:-8080}$(NC)"

web-down: ## Detener interfaz web
	$(COMPOSE_WEB) stop web-service

web-logs: ## Ver logs de la interfaz web
	$(COMPOSE_WEB) logs -f --tail=100 web-service

# --- Utilidades ---
db-shell: ## Abrir sqlite3 en la base de datos de backup
	$(COMPOSE) exec backup-service sqlite3 /data/db/backup.db

git-log: ## Ver historial git del repositorio de metadatos
	$(COMPOSE) exec backup-service git -C /data/git log --oneline --graph -20

health: ## Verificar salud del sistema
	@echo "=== Backup Service ==="
	@curl -sf http://localhost:8001/api/v1/health | python3 -m json.tool 2>/dev/null || echo "$(RED)No disponible$(NC)"
	@echo ""
	@echo "=== Web Service ==="
	@curl -sf http://localhost:$${WEB_PORT:-8080}/health 2>/dev/null || echo "$(RED)No disponible$(NC)"

dev-maildir: ## Crear estructura de prueba con maildir falso
	@mkdir -p dev-maildir/example.com/user1/Maildir/{cur,new,tmp}
	@mkdir -p dev-maildir/example.com/user2/Maildir/{cur,new,tmp}
	@mkdir -p "dev-maildir/example.com/user1/Maildir/.Sent/{cur,new,tmp}"
	@python3 scripts/generate_test_emails.py 2>/dev/null || echo "$(YELLOW)Generar emails de prueba manualmente$(NC)"
	@echo "$(GREEN)✓ Estructura maildir de prueba creada$(NC)"

# --- Desarrollo (maildir local, DEMO_AUTH) ---
.env.dev:
	@echo "$(YELLOW)➜  Creando .env.dev para desarrollo...$(NC)"
	@printf '# %s\n# %s\n# %s\n\nBACKUP_CONFIG=/config/backup.conf\nTZ=America/Argentina/Buenos_Aires\nBACKUP_API_KEY=dev_api_key_1234567890abcdef\nZIMBRA_REMOTE_HOST=localhost\nZIMBRA_REMOTE_USER=devuser\nRSYNC_HOST=localhost\nRSYNC_USER=devuser\nWEB_PORT=8080\nWEB_SECRET_KEY=dev_secret_key_1234567890abcdef\nLDAP_HOST=ldap://localhost\nLDAP_PORT=389\nLDAP_USE_TLS=false\nLDAP_BIND_DN=uid=admin,ou=people,dc=example,dc=com\nLDAP_BIND_PASSWORD=admin123\nLDAP_BASE_DN=ou=people,dc=example,dc=com\nLDAP_USER_FILTER=(mail={username})\nLDAP_GROUP_ADMIN=cn=admins,ou=groups,dc=example,dc=com\nDEMO_AUTH=true\n' > .env.dev
	@echo "$(GREEN)✓ .env.dev creado$(NC)"

dev-up: .env.dev ## Iniciar entorno de desarrollo (maildir local + demo auth)
	@echo "$(YELLOW)➜  Iniciando entorno de DESARROLLO (NO USAR EN PRODUCCIÓN)$(NC)"
	@mkdir -p data/backups data/db data/logs data/git data/weblogs dev-maildir
	@chmod -R 777 data/ dev-maildir/ 2>/dev/null || true
	$(COMPOSE_DEV) up -d --build
	@echo "$(GREEN)✓ Servicios iniciados$(NC)"
	@echo "$(YELLOW)➜  Web: http://localhost:8080$(NC)"
	@echo "$(YELLOW)➜  Login: admin@example.com / admin123$(NC)"
	@echo "$(YELLOW)➜  CLI:  make dev-status$(NC)"

dev-down: ## Detener entorno de desarrollo
	$(COMPOSE_DEV) down

dev-logs: ## Ver logs del entorno de desarrollo
	$(COMPOSE_DEV) logs -f --tail=100

dev-status: ## Mostrar estado del sistema de backup (desarrollo)
	$(CLI_DEV) status

dev-backup: ## Ejecutar backup inmediatamente (desarrollo)
	$(CLI_DEV) backup --trigger manual

dev-shell: ## Abrir shell en el contenedor de backup (desarrollo)
	$(COMPOSE_DEV) exec backup-service bash
