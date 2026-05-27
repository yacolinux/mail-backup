"""Versionado git para metadatos de backup (manifiestos e índices)."""

import json
import logging
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)
UTC = timezone.utc


class GitHandler:
    """Maneja el repositorio git para versionar los metadatos del backup.

    El repositorio git NO contiene los emails en sí (demasiado grandes),
    sino el MANIFIESTO (índice de qué hay backupeado) y metadatos.
    Esto permite ver la evolución histórica del sistema de backup.
    """

    def __init__(self, cfg):
        self.repo_path = cfg.git.repo_path
        self.user_name = cfg.git.user_name
        self.user_email = cfg.git.user_email
        self.git_remote = cfg.git.git_remote
        self._ensure_repo()

    def _git(self, *args, check: bool = True) -> subprocess.CompletedProcess:
        """Ejecuta un comando git en el repositorio."""
        env = os.environ.copy()
        env["GIT_AUTHOR_NAME"] = self.user_name
        env["GIT_AUTHOR_EMAIL"] = self.user_email
        env["GIT_COMMITTER_NAME"] = self.user_name
        env["GIT_COMMITTER_EMAIL"] = self.user_email
        return subprocess.run(
            ["git", "-C", self.repo_path] + list(args),
            capture_output=True,
            text=True,
            env=env,
            check=check,
            timeout=120,
        )

    def _ensure_repo(self):
        """Inicializa el repositorio si no existe."""
        os.makedirs(self.repo_path, exist_ok=True)
        git_dir = Path(self.repo_path) / ".git"

        if not git_dir.exists():
            self._git("config", "--global", "--add", "safe.directory",
                       self.repo_path, check=False)
            try:
                self._git("init")
            except Exception:
                # Retry: force reinit
                import shutil
                shutil.rmtree(str(git_dir), ignore_errors=True)
                self._git("init", check=False)

            self._git("config", "user.name", self.user_name, check=False)
            self._git("config", "user.email", self.user_email, check=False)

            # Crear .gitignore inicial
            gitignore = Path(self.repo_path) / ".gitignore"
            gitignore.write_text("*.tmp\n*.lock\n")

            # Commit inicial
            self._git("add", ".gitignore", check=False)
            self._git(
                "commit", "-m",
                "chore: inicializar repositorio de metadatos",
                check=False,
            )
            logger.info(f"Repositorio git inicializado en {self.repo_path}")

        if self.git_remote:
            try:
                self._git("remote", "add", "origin", self.git_remote, check=False)
            except Exception:
                pass  # Ya puede existir

    def update_manifest(self, accounts: List[Dict], run_info: Dict) -> str:
        """Actualiza el archivo MANIFEST.json con el estado actual del backup.

        Retorna el hash del commit generado.
        """
        manifest = {
            "updated_at": datetime.now(UTC).isoformat(),
            "backup_run": run_info,
            "accounts": [
                {
                    "email": a.get("email"),
                    "domain": a.get("domain"),
                    "total_emails": a.get("total_emails", 0),
                    "last_backup_at": a.get("last_backup_at"),
                }
                for a in accounts
            ],
        }

        manifest_path = Path(self.repo_path) / "MANIFEST.json"
        manifest_path.write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False) + "\n"
        )
        return manifest_path

    def update_account_index(self, email: str, snapshots: List[Dict]):
        """Actualiza el índice de snapshots de una cuenta."""
        safe = email.replace("@", "_at_").replace(".", "_")
        index_dir = Path(self.repo_path) / "accounts"
        index_dir.mkdir(exist_ok=True)

        index_path = index_dir / f"{safe}.json"
        index = {
            "account": email,
            "updated_at": datetime.now(UTC).isoformat(),
            "snapshots": [
                {
                    "name": s.get("snapshot_name"),
                    "type": s.get("snapshot_type"),
                    "email_count": s.get("email_count", 0),
                    "size_bytes": s.get("size_bytes", 0),
                    "created_at": s.get("created_at"),
                }
                for s in snapshots[:50]  # últimos 50 para no inflar el repo
            ],
        }
        index_path.write_text(
            json.dumps(index, indent=2, ensure_ascii=False) + "\n"
        )

    def commit_metadata(self, message: str) -> Optional[str]:
        """Genera un commit con los cambios actuales en el repositorio.

        Retorna el hash del commit o None si no hubo cambios.
        """
        try:
            self._git("add", "-A")

            # Verificar si hay cambios para commitear
            status = self._git("status", "--porcelain")
            if not status.stdout.strip():
                logger.debug("git: sin cambios para commitear")
                return None

            ts = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
            full_message = f"{message}\n\nTimestamp: {ts}"

            self._git("commit", "-m", full_message)

            # Obtener hash del commit
            result = self._git("rev-parse", "--short", "HEAD")
            commit_hash = result.stdout.strip()
            logger.info(f"git commit {commit_hash}: {message}")

            # Push si hay remote configurado
            if self.git_remote:
                self._push()

            return commit_hash

        except subprocess.CalledProcessError as e:
            logger.error(f"Error en git commit: {e.stderr}")
            return None

    def _push(self):
        """Push al remote (falla silenciosamente si no hay conectividad)."""
        try:
            result = self._git("push", "origin", "main", "--force-with-lease",
                               check=False)
            if result.returncode != 0:
                result = self._git("push", "origin", "master", "--force-with-lease",
                                   check=False)
            if result.returncode == 0:
                logger.info("git push OK")
            else:
                logger.warning(f"git push falló (no crítico): {result.stderr}")
        except Exception as e:
            logger.warning(f"git push falló (no crítico): {e}")

    def get_log(self, limit: int = 20) -> List[Dict]:
        """Retorna los últimos N commits del repositorio."""
        try:
            result = self._git(
                "log",
                f"-{limit}",
                "--pretty=format:%H|%h|%ai|%s",
                check=False,
            )
            entries = []
            for line in result.stdout.strip().split("\n"):
                if "|" in line:
                    parts = line.split("|", 3)
                    if len(parts) == 4:
                        entries.append({
                            "hash": parts[0],
                            "short_hash": parts[1],
                            "date": parts[2],
                            "message": parts[3],
                        })
            return entries
        except Exception:
            return []

    def get_diff(self, commit_hash: str) -> str:
        """Retorna el diff de un commit específico."""
        try:
            result = self._git("show", "--stat", commit_hash, check=False)
            return result.stdout
        except Exception:
            return ""
