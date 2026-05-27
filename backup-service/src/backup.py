"""Motor principal de backup: orquesta descubrimiento, copia, indexado y retención."""

import fcntl
import hashlib
import logging
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from .config import BackupConfig
from .db import Database
from .git_handler import GitHandler
from .maildir import scan_maildir, get_maildir_stats
from .retention import RetentionManager
from .rsync_handler import rsync_local, rsync_remote, rsync_from_zimbra

logger = logging.getLogger(__name__)
UTC = timezone.utc

_LOCK_FILE = "/tmp/zimbra_backup.lock"


def _acquire_lock() -> Optional[object]:
    """Adquiere un file lock para prevenir backups concurrentes. Retorna el fd o None si ya está lockeado."""
    try:
        fd = open(_LOCK_FILE, "w")
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fd.write(f"{os.getpid()}\n")
        fd.flush()
        return fd
    except (IOError, OSError):
        return None


def _release_lock(fd):
    """Libera el file lock."""
    try:
        fcntl.flock(fd, fcntl.LOCK_UN)
        fd.close()
    except Exception:
        pass


def _safe_email(email: str) -> str:
    """Convierte email en nombre de directorio seguro con hash anti-colisión."""
    safe = email.replace("@", "_at_").replace(".", "_")
    short_hash = hashlib.sha256(email.lower().encode()).hexdigest()[:8]
    return f"{safe}_{short_hash}"


class BackupEngine:
    """Orquesta el proceso completo de backup de mailboxes Zimbra."""

    def __init__(self, config: BackupConfig, db: Database):
        self.config = config
        self.db = db
        self.git = GitHandler(config) if config.git_enabled else None

    # =========================================================================
    # Descubrimiento de cuentas
    # =========================================================================

    def discover_accounts(self) -> List[Dict]:
        """Descubre todas las cuentas de correo a backupear."""
        if self.config.account_discovery == "zmprov":
            accounts = self._discover_via_zmprov()
        else:
            accounts = self._discover_via_scan()

        # Aplicar filtros
        excluded = set(self.config.exclude_accounts)
        allowed_domains = set(self.config.include_domains)

        filtered = []
        for acc in accounts:
            if acc["email"] in excluded:
                logger.debug(f"Cuenta excluida: {acc['email']}")
                continue
            if allowed_domains and acc["domain"] not in allowed_domains:
                logger.debug(f"Dominio no incluido: {acc['domain']}")
                continue
            filtered.append(acc)

        logger.info(f"Cuentas descubiertas: {len(filtered)}")
        return filtered

    def _discover_via_scan(self) -> List[Dict]:
        """Escanea el directorio base buscando estructura domain/user/Maildir.

        Cuando zimbra_remote está activo, escanea el servidor Zimbra via SSH.
        """
        if self.config.zimbra_remote_enabled:
            return self._discover_via_scan_remote()

        accounts = []
        base = Path(self.config.maildir_base)

        if not base.exists():
            logger.error(f"maildir_base no existe: {base}")
            return accounts

        for domain_dir in sorted(base.iterdir()):
            if not domain_dir.is_dir() or domain_dir.name.startswith("."):
                continue
            domain = domain_dir.name

            for user_dir in sorted(domain_dir.iterdir()):
                if not user_dir.is_dir() or user_dir.name.startswith("."):
                    continue
                username = user_dir.name

                # Buscar Maildir
                maildir = user_dir / "Maildir"
                if not maildir.exists():
                    # Quizás el directorio ES el Maildir (tiene cur/ new/)
                    if (user_dir / "cur").exists() or (user_dir / "new").exists():
                        maildir = user_dir
                    else:
                        logger.debug(f"Sin Maildir en {user_dir}")
                        continue

                accounts.append({
                    "email": f"{username}@{domain}",
                    "domain": domain,
                    "username": username,
                    "maildir_path": str(maildir),
                })

        return accounts

    def _discover_via_scan_remote(self) -> List[Dict]:
        """Escanea el maildir base en el servidor Zimbra remoto via SSH.

        Ejecuta `find` remotamente para localizar todas las estructuras
        <maildir_base>/domain/user/Maildir en el servidor Zimbra.
        """
        zr = self.config.zimbra_remote
        base = self.config.maildir_base.rstrip("/")

        try:
            remote_cmd = (
                f"find {base} -mindepth 3 -maxdepth 3 -name Maildir -type d 2>/dev/null"
            )
            result = subprocess.run(
                [
                    "ssh",
                    "-i", zr.ssh_key,
                    "-p", str(zr.ssh_port),
                    "-o", "StrictHostKeyChecking=accept-new",
                    "-o", "ConnectTimeout=10",
                    "-o", "BatchMode=yes",
                    f"{zr.user}@{zr.host}",
                    remote_cmd,
                ],
                capture_output=True,
                text=True,
                timeout=60,
            )
            if result.returncode not in (0,):
                logger.error(
                    f"Escaneo remoto falló (código {result.returncode}): "
                    f"{result.stderr[-500:]}"
                )
                return []
            accounts = []
            for line in result.stdout.strip().split("\n"):
                line = line.strip()
                if not line:
                    continue
                # Ruta esperada: /zimbra/store/example.com/user1/Maildir
                rel = line[len(base):].strip("/")
                parts = rel.split("/")
                if len(parts) == 3 and parts[2] == "Maildir":
                    domain, username = parts[0], parts[1]
                    accounts.append({
                        "email": f"{username}@{domain}",
                        "domain": domain,
                        "username": username,
                        "maildir_path": line,  # ruta en el servidor Zimbra
                    })
            logger.info(f"Escaneo remoto ({zr.host}): {len(accounts)} cuentas encontradas")
            return accounts
        except Exception as e:
            logger.error(f"Error en escaneo remoto de Zimbra: {e}")
            return []

    def _discover_via_zmprov(self) -> List[Dict]:
        """Obtiene cuentas usando zmprov (local o via SSH según configuración)."""
        zr = self.config.zimbra_remote
        if zr.enabled:
            # Ejecutar zmprov en el servidor Zimbra via SSH
            cmd = [
                "ssh",
                "-i", zr.ssh_key,
                "-p", str(zr.ssh_port),
                "-o", "StrictHostKeyChecking=accept-new",
                "-o", "ConnectTimeout=10",
                "-o", "BatchMode=yes",
                f"{zr.user}@{zr.host}",
                self.config.zimbra_bin, "-l", "gaa",
            ]
        else:
            # Ejecución local (backup-service en el mismo servidor que Zimbra)
            cmd = [self.config.zimbra_bin, "-l", "gaa"]

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=60,
            )
            accounts = []
            for line in result.stdout.strip().split("\n"):
                email = line.strip().lower()
                if "@" not in email:
                    continue
                username, domain = email.split("@", 1)
                maildir_path = str(
                    Path(self.config.maildir_base) / domain / username / "Maildir"
                )
                accounts.append({
                    "email": email,
                    "domain": domain,
                    "username": username,
                    "maildir_path": maildir_path,
                })
            return accounts
        except Exception as e:
            logger.error(f"Error usando zmprov: {e}. Fallback a escaneo.")
            if self.config.zimbra_remote_enabled:
                return self._discover_via_scan_remote()
            return self._discover_via_scan()

    # =========================================================================
    # Ejecución de backup
    # =========================================================================

    def run(self, trigger: str = "scheduled") -> Dict:
        """Ejecuta un ciclo completo de backup para todas las cuentas.

        Flujo:
        1. Descubrir cuentas
        2. Para cada cuenta: rsync + indexar
        3. Aplicar retención
        4. rsync al servidor remoto
        5. Commit git de metadatos
        """
        lock_fd = _acquire_lock()
        if lock_fd is None:
            raise RuntimeError("Ya hay un backup en ejecución. No se puede ejecutar otro en paralelo.")

        try:
            return self._run_inner(trigger)
        finally:
            _release_lock(lock_fd)

    def _run_inner(self, trigger: str) -> Dict:
        run_id = self.db.create_backup_run(trigger)
        ts = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
        logger.info(f"=== Backup run #{run_id} iniciado ({trigger}) - {ts} ===")

        try:
            accounts = self.discover_accounts()
            self.db.update_backup_run(run_id, accounts_total=len(accounts))

            stats = {"success": 0, "failed": 0, "emails_new": 0}

            for account_info in accounts:
                email = account_info["email"]
                try:
                    result = self._backup_account(run_id, account_info)
                    stats["success"] += 1
                    stats["emails_new"] += result.get("emails_new", 0)
                    logger.info(
                        f"  ✓ {email}: {result.get('emails_new', 0)} emails nuevos"
                    )
                except Exception as e:
                    stats["failed"] += 1
                    logger.error(f"  ✗ {email}: {e}", exc_info=True)

            # Aplicar retención global
            retention = RetentionManager(self.config, self.db)
            ret_stats = retention.apply_all()
            logger.info(
                f"Retención: {ret_stats['deleted']} snapshots eliminados, "
                f"{ret_stats['freed_bytes'] / 1024 / 1024:.1f} MB liberados"
            )

            # rsync remoto
            if self.config.remote_enabled:
                try:
                    ok = rsync_remote(self.config.backup_dir, self.config.remote)
                    if ok:
                        logger.info("rsync remoto: OK")
                    else:
                        logger.warning("rsync remoto: falló (no crítico)")
                except Exception as e:
                    logger.error(f"rsync remoto error: {e}")

            # Git commit de metadatos
            if self.git:
                try:
                    all_accounts = self.db.get_all_accounts()
                    self.git.update_manifest(
                        all_accounts,
                        {
                            "run_id": run_id,
                            "trigger": trigger,
                            "accounts_success": stats["success"],
                            "accounts_failed": stats["failed"],
                            "emails_new": stats["emails_new"],
                        },
                    )
                    # Actualizar índices por cuenta
                    for acc in all_accounts:
                        snaps = self.db.get_account_snapshots(acc["id"], limit=50)
                        self.git.update_account_index(acc["email"], snaps)

                    commit_hash = self.git.commit_metadata(
                        f"backup: run #{run_id} - {stats['success']} cuentas OK"
                    )
                    if commit_hash:
                        # Actualizar snapshots con hash de commit
                        pass  # TODO: asociar hash a snapshots del run
                except Exception as e:
                    logger.error(f"Error en git commit: {e}")

            # Finalizar run
            status = (
                "success" if stats["failed"] == 0
                else ("partial" if stats["success"] > 0 else "failed")
            )
            self.db.complete_backup_run(
                run_id,
                status=status,
                accounts_success=stats["success"],
                accounts_failed=stats["failed"],
                emails_new=stats["emails_new"],
            )

            logger.info(
                f"=== Backup run #{run_id} completado: "
                f"{stats['success']} OK, {stats['failed']} errores, "
                f"{stats['emails_new']} emails nuevos ==="
            )
            return {"run_id": run_id, "status": status, **stats}

        except Exception as e:
            self.db.complete_backup_run(run_id, status="failed", error_message=str(e))
            logger.error(f"Backup run #{run_id} falló: {e}", exc_info=True)
            raise

    def _backup_account(self, run_id: int, account_info: Dict) -> Dict:
        """Backup de una cuenta individual.

        Proceso:
        1. Obtener/crear cuenta en DB
        2. Obtener snapshot anterior (para --link-dest)
        3. rsync source → nuevo snapshot
        4. Indexar emails del snapshot
        5. Guardar snapshot en DB
        6. Aplicar retención para esta cuenta
        """
        email = account_info["email"]
        maildir_path = account_info["maildir_path"]

        if not self.config.zimbra_remote_enabled and not os.path.exists(maildir_path):
            raise FileNotFoundError(f"Maildir no existe: {maildir_path}")

        account_id = self.db.get_or_create_account(account_info)
        prev_snapshot = self.db.get_latest_snapshot(account_id)

        # Nombre del snapshot basado en timestamp
        ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        snapshot_name = ts
        snapshot_path = self.config.snapshot_path(email, snapshot_name)
        os.makedirs(snapshot_path, exist_ok=True)

        run_acc_id = self.db.create_run_account(run_id, account_id)
        snapshot_id = None

        try:
            # rsync con hardlinks al snapshot anterior
            prev_path = prev_snapshot["snapshot_path"] if prev_snapshot else None
            if self.config.zimbra_remote_enabled:
                ok = rsync_from_zimbra(
                    self.config.zimbra_remote,
                    maildir_path,
                    snapshot_path,
                    link_dest=prev_path,
                )
            else:
                ok = rsync_local(maildir_path, snapshot_path, link_dest=prev_path)
            if not ok:
                raise RuntimeError(f"rsync falló para {email}")

            # Crear registro de snapshot en DB antes de indexar (para obtener ID)
            snapshot_id = self.db.create_snapshot(
                account_id=account_id,
                snapshot_name=snapshot_name,
                snapshot_path=snapshot_path,
                snapshot_type="hourly",
            )

            # Indexar emails del nuevo snapshot
            emails_new = self._index_snapshot(account_id, snapshot_path, snapshot_id)

            # Calcular estadísticas y actualizar snapshot
            size_bytes = self._calc_snapshot_size(snapshot_path)
            email_count = self.db.count_account_emails(account_id)

            self.db.update_snapshot(snapshot_id, {
                "email_count": email_count,
                "size_bytes": size_bytes,
            })

            self.db.update_account(
                account_id,
                {
                    "last_backup_at": datetime.now(UTC).isoformat(),
                    "total_emails": email_count,
                    "total_size_bytes": size_bytes,
                },
            )
            self.db.complete_run_account(
                run_acc_id, "success", snapshot_id, emails_new
            )

            return {"emails_new": emails_new, "snapshot_id": snapshot_id}

        except Exception as e:
            self.db.complete_run_account(run_acc_id, "failed", error_message=str(e))
            # Limpiar snapshot parcial: eliminar directorio y registro en DB
            snapshot_deleted = False
            if os.path.exists(snapshot_path):
                try:
                    if not os.listdir(snapshot_path):
                        os.rmdir(snapshot_path)
                        snapshot_deleted = True
                    else:
                        shutil.rmtree(snapshot_path, ignore_errors=True)
                        snapshot_deleted = True
                        logger.info(f"Snapshot parcial eliminado: {snapshot_path}")
                except OSError:
                    pass
            if snapshot_deleted and snapshot_id is not None:
                try:
                    self.db.delete_snapshot(snapshot_id)
                except Exception:
                    pass
            raise

    def _index_snapshot(self, account_id: int, snapshot_path: str, snapshot_id: int) -> int:
        """Indexa todos los emails de un snapshot en la base de datos."""
        emails_new = 0

        for email_meta in scan_maildir(snapshot_path):
            filename = email_meta["filename"]
            if not self.db.email_exists(account_id, filename):
                self.db.create_email(account_id, email_meta, snapshot_id)
                emails_new += 1
            else:
                self.db.update_email_last_seen(account_id, filename, snapshot_id)

        return emails_new

    def _calc_snapshot_size(self, path: str) -> int:
        """Calcula el tamaño total de un snapshot (incluyendo hardlinks)."""
        total = 0
        seen_inodes = set()
        for dirpath, _, filenames in os.walk(path):
            for filename in filenames:
                filepath = os.path.join(dirpath, filename)
                try:
                    stat = os.lstat(filepath)
                    inode = (stat.st_dev, stat.st_ino)
                    if inode not in seen_inodes:
                        seen_inodes.add(inode)
                        total += stat.st_size
                except OSError:
                    pass
        return total

    # =========================================================================
    # Eliminación definitiva de emails
    # =========================================================================

    def delete_email_permanently(self, email_id: int) -> Dict:
        """Elimina un email definitivamente de TODOS los snapshots.

        Esta operación es IRREVERSIBLE. El email se elimina del sistema de
        archivos en todos los snapshots donde aparece, y se marca como
        eliminado en la base de datos.
        """
        email_record = self.db.get_email_by_id(email_id)
        if not email_record:
            raise ValueError(f"Email ID {email_id} no encontrado")

        if email_record["deleted"]:
            raise ValueError(f"Email ID {email_id} ya fue eliminado")

        account_id = email_record["account_id"]
        filename = email_record["filename"]

        # Obtener todos los snapshots de la cuenta
        snapshots = self.db.get_account_snapshots(account_id, limit=10000)

        deleted_from = 0
        for snap in snapshots:
            snap_path = snap.get("snapshot_path", "")
            if not snap_path or not os.path.exists(snap_path):
                continue

            # Buscar el archivo en el snapshot
            deleted = self._delete_file_from_snapshot(snap_path, filename)
            if deleted:
                deleted_from += 1

        # Marcar como eliminado en DB
        self.db.mark_email_deleted(email_id)

        # Git commit
        if self.git:
            account = self.db.get_account_by_email(
                email_record.get("account_email", "")
            )
            if account:
                self.git.commit_metadata(
                    f"delete: email {email_id} ({filename}) eliminado "
                    f"de {deleted_from} snapshots"
                )

        logger.info(
            f"Email {email_id} ({filename}) eliminado de {deleted_from} snapshots"
        )
        return {
            "email_id": email_id,
            "filename": filename,
            "deleted_from_snapshots": deleted_from,
        }

    def _delete_file_from_snapshot(self, snapshot_path: str, filename: str) -> bool:
        """Busca y elimina un archivo por nombre en un snapshot."""
        for dirpath, _, filenames in os.walk(snapshot_path):
            if filename in filenames:
                filepath = os.path.join(dirpath, filename)
                try:
                    os.unlink(filepath)
                    return True
                except OSError as e:
                    logger.error(f"Error eliminando {filepath}: {e}")
        return False
