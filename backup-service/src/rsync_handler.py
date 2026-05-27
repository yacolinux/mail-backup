"""Manejo de rsync para copias locales (con hardlinks) y remotas."""

import logging
import os
import subprocess
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def _run_rsync(cmd: list, description: str) -> bool:
    """Ejecuta rsync y loguea el resultado."""
    logger.debug(f"rsync: {' '.join(cmd)}")
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=3600,  # 1 hora máximo por operación
        )
        if result.returncode == 0:
            logger.debug(f"{description}: OK")
            return True
        elif result.returncode == 24:
            # Código 24: algunos archivos desaparecieron durante el proceso (normal en mail activo)
            logger.warning(f"{description}: completado con warnings (archivos modificados durante copia)")
            return True
        else:
            logger.error(
                f"{description} falló (código {result.returncode}):\n"
                f"STDOUT: {result.stdout[-500:]}\n"
                f"STDERR: {result.stderr[-500:]}"
            )
            return False
    except subprocess.TimeoutExpired:
        logger.error(f"{description}: timeout después de 1 hora")
        return False
    except FileNotFoundError:
        logger.error("rsync no está instalado en el sistema")
        return False


def rsync_local(
    source_path: str,
    dest_path: str,
    link_dest: Optional[str] = None,
) -> bool:
    """Copia incremental de maildir usando rsync con hardlinks.

    Usa --link-dest para crear hardlinks a archivos no modificados del
    snapshot anterior, haciendo la copia extremadamente eficiente en disco.

    Args:
        source_path: Ruta fuente (maildir de Zimbra)
        dest_path:   Ruta destino (directorio del nuevo snapshot)
        link_dest:   Snapshot anterior para hardlinks (opcional)
    """
    os.makedirs(dest_path, exist_ok=True)

    # Asegurar que source termine en / para que rsync copie el CONTENIDO
    src = source_path.rstrip("/") + "/"

    cmd = [
        "rsync",
        "--archive",          # equivale a -rlptgoD
        "--delete",           # eliminar en dest lo que no está en src
        "--delete-excluded",
        "--hard-links",       # preservar hardlinks dentro del source
        "--one-file-system",  # no cruzar sistemas de archivos
        "--ignore-errors",    # continuar si hay errores en archivos individuales
        "--exclude=tmp/",     # excluir carpeta tmp de Maildir
        "--exclude=*.lock",
        "--exclude=.DS_Store",
    ]

    if link_dest:
        # link_dest debe ser ruta ABSOLUTA para rsync
        abs_link_dest = os.path.abspath(link_dest)
        if os.path.exists(abs_link_dest):
            cmd.extend(["--link-dest", abs_link_dest])
        else:
            logger.warning(f"link_dest no existe: {abs_link_dest}")

    cmd.extend([src, dest_path])

    return _run_rsync(cmd, f"rsync local {source_path} → {dest_path}")


def rsync_from_zimbra(
    zimbra_cfg,
    remote_maildir_path: str,
    local_dest: str,
    link_dest: Optional[str] = None,
) -> bool:
    """Pull incremental de maildir desde el servidor Zimbra via rsync+SSH.

    Trae el maildir del servidor Zimbra al directorio de snapshot local.
    Equivalente a rsync_local pero el source es el servidor Zimbra remoto.

    Args:
        zimbra_cfg:          Objeto ZimbraRemoteConfig con los datos SSH
        remote_maildir_path: Ruta del maildir EN el servidor Zimbra
        local_dest:          Directorio local donde guardar el snapshot
        link_dest:           Snapshot anterior para hardlinks (opcional)
    """
    os.makedirs(local_dest, exist_ok=True)

    ssh_cmd = [
        "ssh",
        "-i", zimbra_cfg.ssh_key,
        "-p", str(zimbra_cfg.ssh_port),
        "-o", "StrictHostKeyChecking=accept-new",
        "-o", "ConnectTimeout=30",
        "-o", "BatchMode=yes",
    ]

    src = f"{zimbra_cfg.user}@{zimbra_cfg.host}:{remote_maildir_path.rstrip('/')}/"

    cmd = [
        "rsync",
        "--archive",
        "--delete",
        "--delete-excluded",
        "--hard-links",
        "--one-file-system",
        "--ignore-errors",
        "--exclude=tmp/",
        "--exclude=*.lock",
        "--exclude=.DS_Store",
        f"--rsh={' '.join(ssh_cmd)}",
    ]

    if link_dest:
        abs_link_dest = os.path.abspath(link_dest)
        if os.path.exists(abs_link_dest):
            cmd.extend(["--link-dest", abs_link_dest])
        else:
            logger.warning(f"link_dest no existe: {abs_link_dest}")

    cmd.extend([src, local_dest])

    return _run_rsync(
        cmd,
        f"rsync pull {zimbra_cfg.host}:{remote_maildir_path} → {local_dest}",
    )


def rsync_remote(local_backup_dir: str, remote_cfg) -> bool:
    """Sincroniza el directorio de backups al servidor remoto via rsync+SSH.

    Usa SSH con clave privada para autenticación. El servidor remoto debe
    tener rsync instalado y el usuario debe tener permisos de escritura.

    Args:
        local_backup_dir: Directorio local de backups
        remote_cfg: Objeto RemoteConfig con configuración del servidor remoto
    """
    if not remote_cfg.enabled:
        return True

    if not remote_cfg.host or not remote_cfg.user:
        logger.error("Configuración remota incompleta: falta host o user")
        return False

    # Construir opción SSH
    ssh_cmd = [
        "-i", remote_cfg.ssh_key,
        "-p", str(remote_cfg.ssh_port),
        "-o", "StrictHostKeyChecking=accept-new",
        "-o", "ConnectTimeout=30",
        "-o", "BatchMode=yes",
    ]

    dest = f"{remote_cfg.user}@{remote_cfg.host}:{remote_cfg.path}/"
    src = local_backup_dir.rstrip("/") + "/"

    cmd = [
        "rsync",
        "--archive",
        "--delete",
        "--hard-links",
        "--one-file-system",
        "--ignore-errors",
        "--rsh=ssh " + " ".join(ssh_cmd),
    ]

    # Agregar opciones extra del config
    if remote_cfg.rsync_options:
        for opt in remote_cfg.rsync_options.split():
            cmd.append(opt)

    cmd.extend([src, dest])

    logger.info(f"Iniciando rsync remoto → {remote_cfg.host}:{remote_cfg.path}")
    return _run_rsync(cmd, f"rsync remoto → {remote_cfg.host}")


def verify_rsync_available() -> bool:
    """Verifica que rsync esté instalado."""
    try:
        result = subprocess.run(
            ["rsync", "--version"],
            capture_output=True,
            timeout=10,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
