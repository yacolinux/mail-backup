"""Gestión de configuración del sistema de backup Zimbra."""

import configparser
import hashlib
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List


@dataclass
class RetentionConfig:
    """Política de retención tipo GFS (Grandfather-Father-Son)."""
    hourly_keep: int = 48
    daily_keep: int = 30
    weekly_keep: int = 52
    monthly_keep: int = 120


@dataclass
class RemoteConfig:
    """Configuración del servidor remoto de rsync."""
    enabled: bool = False
    host: str = ""
    user: str = ""
    path: str = ""
    ssh_key: str = "/config/ssh/id_rsa"
    ssh_port: int = 22
    rsync_options: str = "--compress"


@dataclass
class ZimbraRemoteConfig:
    """Conexión SSH al servidor Zimbra para pull remoto de mailboxes."""
    enabled: bool = False
    host: str = ""
    user: str = ""
    ssh_key: str = "/config/ssh/id_rsa"
    ssh_port: int = 22


@dataclass
class GitConfig:
    """Configuración del versionado git de metadatos."""
    enabled: bool = True
    repo_path: str = "/data/git"
    git_remote: str = ""
    user_name: str = "Zimbra Backup System"
    user_email: str = "backup@localhost"


@dataclass
class BackupConfig:
    """Configuración principal del sistema de backup."""

    # General
    backup_dir: str = "/data/backups"
    db_path: str = "/data/db/backup.db"
    log_path: str = "/data/logs/backup.log"
    log_level: str = "INFO"
    backup_interval_hours: int = 2
    timezone: str = "UTC"
    api_key: str = "changeme"

    # Source
    maildir_base: str = "/zimbra/store"
    account_discovery: str = "scan"
    zimbra_bin: str = "/opt/zimbra/bin/zmprov"
    exclude_accounts: List[str] = field(default_factory=list)
    include_domains: List[str] = field(default_factory=list)

    # Sub-configs
    remote: RemoteConfig = field(default_factory=RemoteConfig)
    zimbra_remote: ZimbraRemoteConfig = field(default_factory=ZimbraRemoteConfig)
    retention: RetentionConfig = field(default_factory=RetentionConfig)
    git: GitConfig = field(default_factory=GitConfig)

    @property
    def remote_enabled(self) -> bool:
        return self.remote.enabled

    @property
    def zimbra_remote_enabled(self) -> bool:
        return self.zimbra_remote.enabled

    @property
    def git_enabled(self) -> bool:
        return self.git.enabled

    def accounts_dir(self, email: str) -> str:
        """Ruta del directorio de snapshots para una cuenta.
        Usa hash corto para evitar colisiones entre emails similares.
        """
        safe = email.replace("@", "_at_").replace(".", "_")
        short_hash = hashlib.sha256(email.lower().encode()).hexdigest()[:8]
        return str(Path(self.backup_dir) / "accounts" / f"{safe}_{short_hash}")

    def snapshot_path(self, email: str, snapshot_name: str) -> str:
        """Ruta de un snapshot específico."""
        return str(Path(self.accounts_dir(email)) / snapshot_name)


def load_config(config_path: str | None = None) -> BackupConfig:
    """Carga la configuración desde el archivo INI."""
    if config_path is None:
        config_path = os.environ.get("BACKUP_CONFIG", "/config/backup.conf")

    if not os.path.exists(config_path):
        raise FileNotFoundError(f"Archivo de configuración no encontrado: {config_path}")

    parser = configparser.ConfigParser()
    parser.read(config_path)

    cfg = BackupConfig()

    if parser.has_section("general"):
        g = parser["general"]
        cfg.backup_dir = g.get("backup_dir", cfg.backup_dir)
        cfg.db_path = g.get("db_path", cfg.db_path)
        cfg.log_path = g.get("log_path", cfg.log_path)
        cfg.log_level = g.get("log_level", cfg.log_level)
        cfg.backup_interval_hours = g.getint("backup_interval_hours", cfg.backup_interval_hours)
        cfg.timezone = g.get("timezone", cfg.timezone)
        cfg.api_key = g.get("api_key", cfg.api_key)
        # Override api_key from environment variable
        cfg.api_key = os.environ.get("BACKUP_API_KEY", cfg.api_key)

    if parser.has_section("source"):
        s = parser["source"]
        cfg.maildir_base = s.get("maildir_base", cfg.maildir_base)
        cfg.account_discovery = s.get("account_discovery", cfg.account_discovery)
        cfg.zimbra_bin = s.get("zimbra_bin", cfg.zimbra_bin)
        exclude = s.get("exclude_accounts", "")
        cfg.exclude_accounts = [e.strip() for e in exclude.split(",") if e.strip()]
        include_domains = s.get("include_domains", "")
        cfg.include_domains = [d.strip() for d in include_domains.split(",") if d.strip()]

    if parser.has_section("remote"):
        r = parser["remote"]
        cfg.remote = RemoteConfig(
            enabled=r.getboolean("enabled", False),
            host=r.get("host", ""),
            user=r.get("user", ""),
            path=r.get("path", ""),
            ssh_key=r.get("ssh_key", "/config/ssh/id_rsa"),
            ssh_port=r.getint("ssh_port", 22),
            rsync_options=r.get("rsync_options", "--compress"),
        )
        # Override from environment
        cfg.remote.host = os.environ.get("RSYNC_HOST", cfg.remote.host)
        cfg.remote.user = os.environ.get("RSYNC_USER", cfg.remote.user)

    if parser.has_section("zimbra_remote"):
        zr = parser["zimbra_remote"]
        cfg.zimbra_remote = ZimbraRemoteConfig(
            enabled=zr.getboolean("enabled", False),
            host=zr.get("host", ""),
            user=zr.get("user", ""),
            ssh_key=zr.get("ssh_key", "/config/ssh/id_rsa"),
            ssh_port=zr.getint("ssh_port", 22),
        )
        # Override from environment
        cfg.zimbra_remote.host = os.environ.get("ZIMBRA_REMOTE_HOST", cfg.zimbra_remote.host)
        cfg.zimbra_remote.user = os.environ.get("ZIMBRA_REMOTE_USER", cfg.zimbra_remote.user)

    if parser.has_section("retention"):
        ret = parser["retention"]
        cfg.retention = RetentionConfig(
            hourly_keep=ret.getint("hourly_keep", 48),
            daily_keep=ret.getint("daily_keep", 30),
            weekly_keep=ret.getint("weekly_keep", 52),
            monthly_keep=ret.getint("monthly_keep", 120),
        )

    if parser.has_section("git"):
        g = parser["git"]
        cfg.git = GitConfig(
            enabled=g.getboolean("enabled", True),
            repo_path=g.get("repo_path", "/data/git"),
            git_remote=g.get("git_remote", ""),
            user_name=g.get("git_user_name", "Zimbra Backup System"),
            user_email=g.get("git_user_email", "backup@localhost"),
        )

    return cfg


def config_to_dict(cfg: BackupConfig) -> dict:
    """Convierte la configuración a un diccionario serializable."""
    return {
        "general": {
            "backup_dir": cfg.backup_dir,
            "db_path": cfg.db_path,
            "log_path": cfg.log_path,
            "log_level": cfg.log_level,
            "backup_interval_hours": cfg.backup_interval_hours,
            "timezone": cfg.timezone,
            "api_key": "••••••••",
        },
        "source": {
            "maildir_base": cfg.maildir_base,
            "account_discovery": cfg.account_discovery,
            "zimbra_bin": cfg.zimbra_bin,
            "exclude_accounts": ",".join(cfg.exclude_accounts),
            "include_domains": ",".join(cfg.include_domains),
        },
        "zimbra_remote": {
            "enabled": cfg.zimbra_remote.enabled,
            "host": cfg.zimbra_remote.host,
            "user": cfg.zimbra_remote.user,
            "ssh_key": cfg.zimbra_remote.ssh_key,
            "ssh_port": cfg.zimbra_remote.ssh_port,
        },
        "remote": {
            "enabled": cfg.remote.enabled,
            "host": cfg.remote.host,
            "user": cfg.remote.user,
            "path": cfg.remote.path,
            "ssh_key": cfg.remote.ssh_key,
            "ssh_port": cfg.remote.ssh_port,
            "rsync_options": cfg.remote.rsync_options,
        },
        "retention": {
            "hourly_keep": cfg.retention.hourly_keep,
            "daily_keep": cfg.retention.daily_keep,
            "weekly_keep": cfg.retention.weekly_keep,
            "monthly_keep": cfg.retention.monthly_keep,
        },
        "git": {
            "enabled": cfg.git.enabled,
            "repo_path": cfg.git.repo_path,
            "git_remote": cfg.git.git_remote,
            "user_name": cfg.git.user_name,
            "user_email": cfg.git.user_email,
        },
    }


def save_config(config_path: str, data: dict):
    """Guarda la configuración desde un diccionario al archivo INI."""
    parser = configparser.ConfigParser()
    parser.read(config_path)

    section_map = {
        "general": ["backup_dir", "db_path", "log_path", "log_level",
                     "backup_interval_hours", "timezone", "api_key"],
        "source": ["maildir_base", "account_discovery", "zimbra_bin",
                    "exclude_accounts", "include_domains"],
        "zimbra_remote": ["enabled", "host", "user", "ssh_key", "ssh_port"],
        "remote": ["enabled", "host", "user", "path", "ssh_key",
                    "ssh_port", "rsync_options"],
        "retention": ["hourly_keep", "daily_keep", "weekly_keep", "monthly_keep"],
        "git": ["enabled", "repo_path", "git_remote", "user_name", "user_email"],
    }

    for section, fields in section_map.items():
        if section not in data:
            continue
        if not parser.has_section(section):
            parser.add_section(section)
        for field in fields:
            if field in data[section]:
                val = data[section][field]
                if isinstance(val, bool):
                    val = "true" if val else "false"
                parser[section][field] = str(val)

    with open(config_path, "w") as f:
        parser.write(f)
