# Desarrollo Rápido

## Opción 1: Script automático (recomendado)

```bash
./scripts/dev-init.sh
```

Este script:
1. Verifica la configuración
2. Genera emails de prueba si no existen
3. Crea clave SSH para desarrollo
4. Inicia los servicios
5. Muestra información de acceso

## Opción 2: Manual con Make

```bash
# 1. Iniciar servicios (maildir local + demo auth)
make dev-up

# 2. Verificar estado
make dev-status

# 3. Ejecutar primer backup
make dev-backup
```

## Acceso

| Servicio | URL | Credenciales |
|----------|-----|--------------|
| Web Interface | http://localhost:8080 | Ver abajo |
| API REST | http://localhost:8001 | API key en .env.dev |

## Usuarios Demo

| Email | Password | Rol |
|-------|----------|-----|
| `admin@example.com` | `admin123` | Administrador |
| `user1@example.com` | `user123` | Usuario |
| `user2@example.com` | `user123` | Usuario |

## Comandos Útiles

```bash
make dev-status      # Estado del sistema
make dev-backup      # Ejecutar backup manual
make dev-logs        # Ver logs en tiempo real
make dev-shell       # Shell en el contenedor
make dev-down        # Detener servicios
```

## Datos de Prueba

El entorno incluye:
- 3 cuentas: admin, user1, user2 @example.com
- ~80 emails en total distribuidos en las cuentas
- Carpetas: INBOX, Sent, Trash, Work, Personal
- Emails con fechas aleatorias en los últimos 180 días

## Archivos de Configuración (Desarrollo)

| Archivo | Propósito |
|---------|-----------|
| `.env.dev` | Variables de entorno (DEMO_AUTH=true) |
| `config/backup.dev.conf` | Configuración backup (zimbra_remote.enabled=false) |
| `docker-compose.dev.yml` | Docker Compose con maildir local montado |

## Importante

**NO USAR EN PRODUCCIÓN**. Este entorno:
- Usa autenticación demo (sin LDAP real)
- Accede a maildir local (no remoto)
- Tiene claves API hardcodeadas
- Expone puertos sin seguridad
