FROM python:3.12-slim-bookworm

LABEL maintainer="zimbra-backup-system"
LABEL description="Zimbra Backup Service - daemon + CLI + API REST"

# Dependencias del sistema
RUN apt-get update && apt-get install -y --no-install-recommends \
    rsync \
    git \
    ssh \
    openssh-client \
    sqlite3 \
    && rm -rf /var/lib/apt/lists/*

# Crear usuario no-root para el servicio
RUN groupadd -r backupd && useradd -r -g backupd -m -d /home/backupd backupd

# Directorio de trabajo
WORKDIR /app

# Instalar dependencias Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copiar código fuente
COPY src/ ./src/
COPY entrypoint.sh ./
COPY zbackup /usr/local/bin/zbackup

# Permisos de ejecución
RUN chmod +x /usr/local/bin/zbackup entrypoint.sh

# Crear directorios de datos (serán montados como volúmenes)
RUN mkdir -p /data/backups/accounts /data/db /data/logs /data/git /config/ssh \
    && chown -R backupd:backupd /data /app

# Configurar git globalmente para el usuario del servicio
RUN git config --global --add safe.directory /data/git \
    && git config --global init.defaultBranch main

# Permisos SSH
RUN chmod 700 /config/ssh

# Exponer puerto de la API REST interna
EXPOSE 8001

# Variables de entorno por defecto
ENV BACKUP_CONFIG=/config/backup.conf \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=UTC

USER backupd

ENTRYPOINT ["./entrypoint.sh"]
CMD ["daemon"]
