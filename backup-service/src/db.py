"""Operaciones de base de datos SQLite para el sistema de backup Zimbra."""

import sqlite3
import logging
import os
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional

logger = logging.getLogger(__name__)
UTC = timezone.utc

SCHEMA = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;
PRAGMA synchronous = NORMAL;

CREATE TABLE IF NOT EXISTS accounts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    email           TEXT    UNIQUE NOT NULL,
    domain          TEXT    NOT NULL,
    username        TEXT    NOT NULL,
    maildir_path    TEXT    NOT NULL,
    first_backup_at TEXT,
    last_backup_at  TEXT,
    total_emails    INTEGER DEFAULT 0,
    total_size_bytes INTEGER DEFAULT 0,
    active          INTEGER DEFAULT 1,
    created_at      TEXT    DEFAULT (datetime('now', 'utc'))
);

CREATE TABLE IF NOT EXISTS snapshots (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id      INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    snapshot_name   TEXT    NOT NULL,
    snapshot_path   TEXT    NOT NULL,
    snapshot_type   TEXT    NOT NULL CHECK(snapshot_type IN ('hourly','daily','weekly','monthly')),
    email_count     INTEGER DEFAULT 0,
    size_bytes      INTEGER DEFAULT 0,
    rsync_local     TEXT    DEFAULT 'ok',
    rsync_remote    TEXT    DEFAULT 'pending',
    git_commit      TEXT,
    created_at      TEXT    DEFAULT (datetime('now', 'utc')),
    UNIQUE(account_id, snapshot_name)
);

CREATE TABLE IF NOT EXISTS emails (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id          INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    message_id          TEXT,
    subject             TEXT,
    from_addr           TEXT,
    to_addr             TEXT,
    date                TEXT,
    size_bytes          INTEGER DEFAULT 0,
    folder              TEXT    DEFAULT 'INBOX',
    filename            TEXT    NOT NULL,
    maildir_flags       TEXT    DEFAULT '',
    first_snapshot_id   INTEGER REFERENCES snapshots(id),
    last_snapshot_id    INTEGER REFERENCES snapshots(id),
    deleted             INTEGER DEFAULT 0,
    deleted_at          TEXT,
    created_at          TEXT    DEFAULT (datetime('now', 'utc')),
    UNIQUE(account_id, filename)
);

CREATE TABLE IF NOT EXISTS backup_runs (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at          TEXT    NOT NULL,
    completed_at        TEXT,
    status              TEXT    DEFAULT 'running'
                                CHECK(status IN ('running','success','partial','failed')),
    trigger             TEXT    DEFAULT 'scheduled'
                                CHECK(trigger IN ('scheduled','manual')),
    accounts_total      INTEGER DEFAULT 0,
    accounts_success    INTEGER DEFAULT 0,
    accounts_failed     INTEGER DEFAULT 0,
    emails_new          INTEGER DEFAULT 0,
    error_message       TEXT
);

CREATE TABLE IF NOT EXISTS run_accounts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id      INTEGER NOT NULL REFERENCES backup_runs(id) ON DELETE CASCADE,
    account_id  INTEGER NOT NULL REFERENCES accounts(id),
    snapshot_id INTEGER REFERENCES snapshots(id),
    status      TEXT    DEFAULT 'pending',
    started_at  TEXT,
    completed_at TEXT,
    emails_new  INTEGER DEFAULT 0,
    error_message TEXT
);

-- Índices para búsquedas frecuentes
CREATE INDEX IF NOT EXISTS idx_emails_account   ON emails(account_id);
CREATE INDEX IF NOT EXISTS idx_emails_date      ON emails(date);
CREATE INDEX IF NOT EXISTS idx_emails_deleted   ON emails(deleted);
CREATE INDEX IF NOT EXISTS idx_emails_from      ON emails(from_addr);
CREATE INDEX IF NOT EXISTS idx_snapshots_account ON snapshots(account_id);
CREATE INDEX IF NOT EXISTS idx_snapshots_created ON snapshots(created_at);
CREATE INDEX IF NOT EXISTS idx_run_acc_run      ON run_accounts(run_id);
"""


def _escape_like(value: str) -> str:
    """Escapa caracteres comodín de LIKE (% y _) para búsquedas seguras."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class Database:
    """Gestión de la base de datos SQLite con WAL para concurrencia."""

    _ACCOUNT_COLUMNS = {
        "last_backup_at", "total_emails", "total_size_bytes", "active",
    }
    _SNAPSHOT_COLUMNS = {
        "email_count", "size_bytes", "rsync_local", "rsync_remote", "git_commit",
        "snapshot_type",
    }
    _BACKUP_RUN_COLUMNS = {
        "status", "completed_at", "accounts_total", "accounts_success",
        "accounts_failed", "emails_new", "error_message",
    }

    def __init__(self, db_path: str):
        self.db_path = db_path
        os.makedirs(Path(db_path).parent, exist_ok=True)
        self._init_schema()

    def _init_schema(self):
        with self.conn() as con:
            con.executescript(SCHEMA)

    @contextmanager
    def conn(self) -> Generator[sqlite3.Connection, None, None]:
        con = sqlite3.connect(self.db_path, timeout=30)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys = ON")
        try:
            yield con
            con.commit()
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()

    def _now(self) -> str:
        return datetime.now(UTC).isoformat()

    # =========================================================================
    # Accounts
    # =========================================================================

    def get_or_create_account(self, info: Dict[str, str]) -> int:
        """Retorna el ID de la cuenta, creándola si no existe."""
        with self.conn() as con:
            row = con.execute(
                "SELECT id FROM accounts WHERE email = ?", (info["email"],)
            ).fetchone()
            if row:
                return row["id"]
            cur = con.execute(
                """INSERT INTO accounts (email, domain, username, maildir_path, first_backup_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (info["email"], info["domain"], info["username"],
                 info["maildir_path"], self._now()),
            )
            return cur.lastrowid

    def update_account(self, account_id: int, data: Dict[str, Any]):
        safe_data = {k: v for k, v in data.items() if k in self._ACCOUNT_COLUMNS}
        if not safe_data:
            return
        fields = ", ".join(f"{k} = ?" for k in safe_data)
        values = list(safe_data.values()) + [account_id]
        with self.conn() as con:
            con.execute(f"UPDATE accounts SET {fields} WHERE id = ?", values)

    def get_account_by_email(self, email: str) -> Optional[Dict]:
        with self.conn() as con:
            row = con.execute(
                "SELECT * FROM accounts WHERE email = ?", (email,)
            ).fetchone()
            return dict(row) if row else None

    _ACCOUNT_SORT_COLUMNS = {"email", "domain", "total_emails", "last_backup_at", "active"}

    def get_all_accounts(
        self, search: str = None, limit: int = None, offset: int = 0,
        sort_by: str = "email", sort_order: str = "ASC",
    ) -> List[Dict]:
        conds = ["active = 1"]
        params: List[Any] = []
        if search:
            search_escaped = _escape_like(search)
            conds.append("email LIKE ? ESCAPE '\\'")
            params.append(f"%{search_escaped}%")
        where = " AND ".join(conds)
        col = sort_by if sort_by in self._ACCOUNT_SORT_COLUMNS else "email"
        order = "DESC" if sort_order.upper() == "DESC" else "ASC"
        query = f"SELECT * FROM accounts WHERE {where} ORDER BY {col} {order}"
        if limit is not None:
            query += " LIMIT ? OFFSET ?"
            params.extend([limit, offset])
        with self.conn() as con:
            rows = con.execute(query, params).fetchall()
            return [dict(r) for r in rows]

    def count_all_accounts(self, search: str = None) -> int:
        conds = ["active = 1"]
        params: List[Any] = []
        if search:
            search_escaped = _escape_like(search)
            conds.append("email LIKE ? ESCAPE '\\'")
            params.append(f"%{search_escaped}%")
        where = " AND ".join(conds)
        with self.conn() as con:
            row = con.execute(
                f"SELECT COUNT(*) as c FROM accounts WHERE {where}", params
            ).fetchone()
            return row["c"]

    def count_account_emails(self, account_id: int) -> int:
        with self.conn() as con:
            row = con.execute(
                "SELECT COUNT(*) as c FROM emails WHERE account_id = ? AND deleted = 0",
                (account_id,),
            ).fetchone()
            return row["c"]

    # =========================================================================
    # Snapshots
    # =========================================================================

    def create_snapshot(
        self,
        account_id: int,
        snapshot_name: str,
        snapshot_path: str,
        snapshot_type: str,
        email_count: int = 0,
        size_bytes: int = 0,
    ) -> int:
        with self.conn() as con:
            cur = con.execute(
                """INSERT INTO snapshots
                   (account_id, snapshot_name, snapshot_path, snapshot_type,
                    email_count, size_bytes)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (account_id, snapshot_name, snapshot_path, snapshot_type,
                 email_count, size_bytes),
            )
            return cur.lastrowid

    def update_snapshot(self, snapshot_id: int, data: Dict[str, Any]):
        safe_data = {k: v for k, v in data.items() if k in self._SNAPSHOT_COLUMNS}
        if not safe_data:
            return
        fields = ", ".join(f"{k} = ?" for k in safe_data)
        values = list(safe_data.values()) + [snapshot_id]
        with self.conn() as con:
            con.execute(f"UPDATE snapshots SET {fields} WHERE id = ?", values)

    def get_latest_snapshot(self, account_id: int) -> Optional[Dict]:
        with self.conn() as con:
            row = con.execute(
                """SELECT * FROM snapshots WHERE account_id = ?
                   ORDER BY created_at DESC LIMIT 1""",
                (account_id,),
            ).fetchone()
            return dict(row) if row else None

    def get_account_snapshots(
        self, account_id: int, limit: int = 200
    ) -> List[Dict]:
        with self.conn() as con:
            rows = con.execute(
                """SELECT * FROM snapshots WHERE account_id = ?
                   ORDER BY created_at DESC LIMIT ?""",
                (account_id, limit),
            ).fetchall()
            return [dict(r) for r in rows]

    def delete_snapshot(self, snapshot_id: int):
        with self.conn() as con:
            con.execute("DELETE FROM snapshots WHERE id = ?", (snapshot_id,))

    def update_snapshot_type(self, snapshot_id: int, snapshot_type: str):
        with self.conn() as con:
            con.execute(
                "UPDATE snapshots SET snapshot_type = ? WHERE id = ?",
                (snapshot_type, snapshot_id),
            )

    # =========================================================================
    # Emails
    # =========================================================================

    def email_exists(self, account_id: int, filename: str) -> bool:
        with self.conn() as con:
            row = con.execute(
                "SELECT id FROM emails WHERE account_id = ? AND filename = ?",
                (account_id, filename),
            ).fetchone()
            return row is not None

    def create_email(self, account_id: int, email_data: Dict, snapshot_id: int = None) -> int:
        with self.conn() as con:
            cur = con.execute(
                """INSERT OR IGNORE INTO emails
                   (account_id, message_id, subject, from_addr, to_addr,
                    date, size_bytes, folder, filename, maildir_flags,
                    first_snapshot_id, last_snapshot_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    account_id,
                    email_data.get("message_id", ""),
                    email_data.get("subject", ""),
                    email_data.get("from_addr", ""),
                    email_data.get("to_addr", ""),
                    email_data.get("date", ""),
                    email_data.get("size_bytes", 0),
                    email_data.get("folder", "INBOX"),
                    email_data.get("filename", ""),
                    email_data.get("maildir_flags", ""),
                    snapshot_id,
                    snapshot_id,
                ),
            )
            return cur.lastrowid

    def update_email_last_seen(self, account_id: int, filename: str, snapshot_id: int = None):
        with self.conn() as con:
            if snapshot_id:
                con.execute(
                    """UPDATE emails SET last_snapshot_id = ?
                       WHERE account_id = ? AND filename = ?""",
                    (snapshot_id, account_id, filename),
                )

    _EMAIL_SORT_COLUMNS = {"date", "from_addr", "subject", "folder", "size_bytes"}

    def get_account_emails(
        self,
        account_id: int,
        folder: str = None,
        search: str = None,
        date_from: str = None,
        date_to: str = None,
        include_deleted: bool = False,
        limit: int = 100,
        offset: int = 0,
        sort_by: str = "date",
        sort_order: str = "DESC",
    ) -> List[Dict]:
        conds = ["account_id = ?"]
        params: List[Any] = [account_id]

        if not include_deleted:
            conds.append("deleted = 0")
        if folder:
            conds.append("folder = ?")
            params.append(folder)
        if search:
            escaped = _escape_like(search)
            conds.append("(subject LIKE ? ESCAPE '\\' OR from_addr LIKE ? ESCAPE '\\')")
            params.extend([f"%{escaped}%", f"%{escaped}%"])
        if date_from:
            conds.append("date >= ?")
            params.append(date_from)
        if date_to:
            conds.append("date <= ?")
            params.append(date_to)

        where = " AND ".join(conds)
        params.extend([limit, offset])

        col = sort_by if sort_by in self._EMAIL_SORT_COLUMNS else "date"
        order = "DESC" if sort_order.upper() == "DESC" else "ASC"

        with self.conn() as con:
            rows = con.execute(
                f"""SELECT * FROM emails WHERE {where}
                    ORDER BY {col} {order} LIMIT ? OFFSET ?""",
                params,
            ).fetchall()
            return [dict(r) for r in rows]

    def count_account_emails_filtered(
        self,
        account_id: int,
        folder: str = None,
        search: str = None,
        include_deleted: bool = False,
    ) -> int:
        conds = ["account_id = ?"]
        params: List[Any] = [account_id]
        if not include_deleted:
            conds.append("deleted = 0")
        if folder:
            conds.append("folder = ?")
            params.append(folder)
        if search:
            escaped = _escape_like(search)
            conds.append("(subject LIKE ? ESCAPE '\\' OR from_addr LIKE ? ESCAPE '\\')")
            params.extend([f"%{escaped}%", f"%{escaped}%"])
        where = " AND ".join(conds)
        with self.conn() as con:
            row = con.execute(
                f"SELECT COUNT(*) as c FROM emails WHERE {where}", params
            ).fetchone()
            return row["c"]

    def get_email_by_id(self, email_id: int) -> Optional[Dict]:
        with self.conn() as con:
            row = con.execute(
                """SELECT e.*, a.email as account_email
                   FROM emails e
                   JOIN accounts a ON e.account_id = a.id
                   WHERE e.id = ?""",
                (email_id,),
            ).fetchone()
            return dict(row) if row else None

    def mark_email_deleted(self, email_id: int):
        with self.conn() as con:
            con.execute(
                "UPDATE emails SET deleted = 1, deleted_at = ? WHERE id = ?",
                (self._now(), email_id),
            )

    def get_account_folders(self, account_id: int) -> List[str]:
        with self.conn() as con:
            rows = con.execute(
                """SELECT DISTINCT folder FROM emails
                   WHERE account_id = ? AND deleted = 0
                   ORDER BY folder""",
                (account_id,),
            ).fetchall()
            return [r["folder"] for r in rows]

    # =========================================================================
    # Backup Runs
    # =========================================================================

    def create_backup_run(self, trigger: str = "scheduled") -> int:
        with self.conn() as con:
            cur = con.execute(
                "INSERT INTO backup_runs (started_at, trigger) VALUES (?, ?)",
                (self._now(), trigger),
            )
            return cur.lastrowid

    def update_backup_run(self, run_id: int, **kwargs):
        safe_kwargs = {k: v for k, v in kwargs.items() if k in self._BACKUP_RUN_COLUMNS}
        if not safe_kwargs:
            return
        fields = ", ".join(f"{k} = ?" for k in safe_kwargs)
        values = list(safe_kwargs.values()) + [run_id]
        with self.conn() as con:
            con.execute(f"UPDATE backup_runs SET {fields} WHERE id = ?", values)

    def complete_backup_run(
        self,
        run_id: int,
        status: str,
        accounts_success: int = 0,
        accounts_failed: int = 0,
        emails_new: int = 0,
        error_message: str = None,
    ):
        with self.conn() as con:
            con.execute(
                """UPDATE backup_runs SET
                   completed_at = ?, status = ?,
                   accounts_success = ?, accounts_failed = ?,
                   emails_new = ?, error_message = ?
                   WHERE id = ?""",
                (self._now(), status, accounts_success, accounts_failed,
                 emails_new, error_message, run_id),
            )

    def get_recent_runs(self, limit: int = 10) -> List[Dict]:
        with self.conn() as con:
            rows = con.execute(
                "SELECT * FROM backup_runs ORDER BY started_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]

    def create_run_account(self, run_id: int, account_id: int) -> int:
        with self.conn() as con:
            cur = con.execute(
                """INSERT INTO run_accounts (run_id, account_id, status, started_at)
                   VALUES (?, ?, 'running', ?)""",
                (run_id, account_id, self._now()),
            )
            return cur.lastrowid

    def complete_run_account(
        self,
        run_acc_id: int,
        status: str,
        snapshot_id: int = None,
        emails_new: int = 0,
        error_message: str = None,
    ):
        with self.conn() as con:
            con.execute(
                """UPDATE run_accounts SET
                   status = ?, snapshot_id = ?, emails_new = ?,
                   completed_at = ?, error_message = ?
                   WHERE id = ?""",
                (status, snapshot_id, emails_new, self._now(),
                 error_message, run_acc_id),
            )

    # =========================================================================
    # Stats
    # =========================================================================

    def get_system_stats(self) -> Dict:
        with self.conn() as con:
            total_accounts = con.execute(
                "SELECT COUNT(*) as c FROM accounts WHERE active = 1"
            ).fetchone()["c"]
            total_emails = con.execute(
                "SELECT COUNT(*) as c FROM emails WHERE deleted = 0"
            ).fetchone()["c"]
            total_size = con.execute(
                "SELECT COALESCE(SUM(size_bytes), 0) as s FROM snapshots"
            ).fetchone()["s"]
            last_run = con.execute(
                "SELECT * FROM backup_runs ORDER BY started_at DESC LIMIT 1"
            ).fetchone()
            total_snapshots = con.execute(
                "SELECT COUNT(*) as c FROM snapshots"
            ).fetchone()["c"]
        return {
            "total_accounts": total_accounts,
            "total_emails": total_emails,
            "total_size_bytes": total_size,
            "total_snapshots": total_snapshots,
            "last_run": dict(last_run) if last_run else None,
        }
