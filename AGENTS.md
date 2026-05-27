# AGENTS.md — Project Context for AI Coding Assistants

## Project

Zimbra Backup System — automated backup for Zimbra 9 / Postfix mailboxes (Maildir format) with GFS retention, rsync hardlinks, web UI with LDAP auth, Docker Compose.

## Key URL

- Web interface (dev): `http://localhost:8080`
- API (dev): `http://localhost:8001`
- Demo users: `admin@example.com` / `admin123`, `user1@example.com` / `user123`, `user2@example.com` / `user123`

## Commands

```bash
# Dev (local maildir, demo auth — NO Zimbra needed)
make dev-up          # build + start backup-service + web-service
make dev-down        # stop + remove
make dev-backup      # run manual backup
make dev-status      # CLI status
make dev-logs        # tail logs
make dev-shell       # shell in backup container

# Rebuild after code changes
sudo docker compose -f docker-compose.dev.yml up -d --build
```

## Architecture

```
backup-zimbra/
├── backup-service/src/      # Python: daemon + CLI + API REST
│   ├── config.py            # BackupConfig, load_config(), save_config()
│   ├── db.py                # SQLite (WAL) — accounts, snapshots, emails, runs
│   ├── backup.py            # BackupEngine — discover, rsync, index, retention
│   ├── retention.py         # GFS policy (hourly/daily/weekly/monthly)
│   ├── rsync_handler.py     # rsync_local, rsync_from_zimbra, rsync_remote
│   ├── maildir.py           # parse_email_headers, scan_maildir, get_email_content
│   ├── converters.py        # email → MD, PDF, DOCX (reportlab, python-docx)
│   ├── git_handler.py       # Git metadata versioning
│   ├── api.py               # Flask API (create_app factory, routes inside it)
│   ├── daemon.py            # APScheduler daemon
│   └── cli.py               # Click CLI (zbackup commands)
├── web-service/src/         # Flask web UI
│   ├── app.py               # Routes, auth guards, SMTP send, download proxies
│   ├── auth.py              # LDAP auth + demo mode
│   ├── api_client.py        # BackupAPIClient → backup-service:8001
│   └── templates/           # Bootstrap 5 templates
├── config/                  # backup.conf, web.json, ssh keys
├── dev-maildir/             # Test maildir (70 accounts, ~5620 emails)
├── scripts/                 # generate_test_emails.py, setup-ssh-key.sh
├── docker-compose.yml       # Production (remote Zimbra)
├── docker-compose.dev.yml   # Development (local maildir, demo auth)
├── docker-compose.web.yml   # Web service addon
└── Makefile                 # dev-up, dev-backup, dev-status, etc.
```

## Data flow — email list

```
Browser → web:8080 → api_client → API:8001 → db.py (SQLite)
                                                  ↓
  emails.html  ← render_template  ← email_list()
```

## Data flow — email download/conversion

```
Browser → /email/<id>/download/<fmt>
  → app.py email_download()
    → api_client.download_email(id, fmt)
      → API GET /emails/<id>/download?format=fmt
        → find_email_file() → converters.convert_email() → send_file
```

## Key patterns

- **Config**: loaded by `load_config()`, env vars override INI file values, `save_config()` writes changes back. Auth config (LDAP + local users) stored in `web.json`, loaded via `_load_web_config()`.
- **DB**: `Database` class with `conn()` context manager, WAL mode, `_escape_like()` for search inputs, whitelisted column names for UPDATE queries
- **API**: `create_app(config, db)` factory, routes inside it use `app.config["BACKUP_DB"]`, `_require_api_key` decorator. Reset endpoints (`/api/v1/reset/*`) call `_wipe_backup_data()` + re-init.
- **WEB**: `@login_required` and `@admin_required` decorators, CSRF via `@app.before_request`, `csrf_token()` in all forms. `DEMO_AUTH` flag controls auth mode. Local demo users in `web.json` → `local_users`.
- **i18n**: English (default) / Spanish via `web-service/src/i18n.py` (150+ keys). Language detected from `zimbra_lang` cookie, toggled via navbar button. `t()` function injected into all templates via context processor. JS `toggleLanguage()` persists in localStorage + cookie.
- **Pagination**: Dashboard accounts 35/page, email list 35/page, sortable columns with query params (`sort`, `order`)
- **Sort**: DB whitelisted columns (`_EMAIL_SORT_COLUMNS`, `_ACCOUNT_SORT_COLUMNS`), API passes `sort_by`/`sort_order`, web reads from query params, JS `sortBy()` / `accSortBy()` mutate URL
- **Resizable sidebars**: CSS custom properties (`--sidebar-width`, `--folder-sidebar-width`), drag handle, localStorage persistence. Collapse button in navbar toggles sidebar visibility.
- **Dark mode**: `[data-theme="dark"]` CSS selector on `<html>`, all colors via CSS custom properties (`--body-bg`, `--card-bg`, `--text-color`, etc.). Moon/sun toggle in navbar, persisted in `localStorage.zimbra_theme`.
- **Config modal**: Single-page modal with 10 tabs (General, Origen, Zimbra Remote, Offsite, Retención, Git, LDAP, Usuarios Locales, Reset, Backup de Configuración). Backup config via `admin_config_load/save`, auth config via `admin_config_auth/save`. LDAP test buttons call `admin_test_ldap_bind/filter`. Local users CRUD via JS + `collectAuthConfig()`. Reset operations call `admin_factory_reset` / `admin_example_reset`. Config export/import via `admin_config_export` / `admin_config_import`.
- **Email conversion**: `converters.py` → `email_to_markdown_bytes`, `email_to_pdf_bytes`, `email_to_docx_bytes`. Naming: `<Subject> -restored<YYYYMMDD> -mailde<YYYYMMDD>.<ext>`
- **Manual**: `web-service/src/manual.md` served at `/manual`, embedded as PDF via iframe (`/manual/pdf`), downloadable as .md and .pdf via `POST /api/v1/utils/md-to-pdf`
- **Log viewer**: `GET /api/v1/logs?lines=N` reads backup log file. Admin-only page at `/admin/logs` with level selector and color-coded log display.
- **Config export/import**: `POST /api/v1/config/export` with optional `password` for AES-256-CBC encrypted ZIP. `POST /api/v1/config/import` accepts JSON or encrypted ZIP, auto-detects format.

## Data flow — config save

```
Config Modal → collectConfig() → POST /admin/config/save → API POST /config → save_config() → INI file
             → collectAuthConfig() → POST /admin/config/auth/save → write web.json
```

## Data flow — reset

```
Reset Modal → POST /admin/reset/example → API POST /reset/example
  → _wipe_backup_data() → rm snapshots + DB + git → reinit all
  → subprocess generate_test_emails.py → run BackupEngine → return
```

## Dev environment details

- Config mount: `./config/backup.dev.conf:/config/backup.conf:rw` (writable for admin config editing)
- Web config: `./config/web.json:/config/web.json:rw` (writable for auth config editing, permissions 666)
- Test data: `./dev-maildir` mounted at `/zimbra/store:rw`, 70 accounts generated by `scripts/generate_test_emails.py`
- Scripts mount: `./scripts:/scripts:ro` (for reset/deploy operations)
- Data dirs: `./data/*` bind mounts (not named volumes) — `chmod 777` for container write access
- API key: `dev_api_key_1234567890abcdef`
- Docker user: `backupd` (UID 999) in backup-service, `webuser` in web-service
- Bootstrap: `make setup` creates all dirs with proper permissions; `make dev-up` ready from clean clone
