# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-05-26

### Initial Release

#### Core Features
- Automated Zimbra 9 / Postfix mailbox backup with rsync `--link-dest` hardlink snapshots
- GFS (Grandfather-Father-Son) retention policy: hourly / daily / weekly / monthly
- Remote pull-mode: backup-service connects via SSH/rsync to the Zimbra server
- Local mode for development with `dev-maildir/`
- Account discovery via `zmprov` (remote SSH or local) or filesystem scan
- SQLite database with WAL mode for concurrent reads
- Git versioning for backup manifests and metadata
- CLI tool (`zbackup`) with 8 commands: status, list-accounts, list-emails, list-backups, backup, prune, delete-email, daemon
- REST API (port 8001) protected with X-API-Key header
- Web interface (port 8080) with LDAP authentication and demo mode
- Multi-select email export as ZIP, email resend via SMTP
- Admin panel: trigger backups, apply retention, git log viewer
- Role-based access: users see own emails only; admins see all + can delete emails
- Docker Compose setup with persistent volumes

### Security Fixes (applied during initial review)

- **db.py**: Whitelist allowed column names in `update_account()`, `update_snapshot()`, `update_backup_run()` to prevent SQL injection via dict keys. Escape LIKE wildcards (`%`, `_`) in search queries with `ESCAPE '\\'` clause.
- **rsync_handler.py**: Build SSH options as a proper list for `--rsh` instead of shell-joined string, preventing injection via paths with spaces or special characters.
- **app.py**: Add CSRF token validation (`before_request` hook) on all POST forms; inject `csrf_token()` into all 6 form templates (login, email send, download ZIP, resend, delete, admin actions). Refuse to start in production without `WEB_SECRET_KEY` env var.
- **api.py**: Replace module-level `_config`/`_db` globals with Flask `app.config[]` via `create_app()` factory pattern, eliminating shared-state race conditions between daemon process and API. Reduce bulk download limit from 500 to 200 emails. Enable `threaded=True` in `run_api()`.

### Bug Fixes (applied during initial review)

- **backup.py (`_discover_via_scan_remote`)**: Check SSH `returncode` before parsing stdout; return empty list on failure instead of potentially parsing error output as accounts.
- **backup.py (`_discover_via_zmprov`)**: Fix fallback to call `_discover_via_scan_remote()` when `zimbra_remote_enabled=True` instead of always calling local `_discover_via_scan()`.
- **backup.py (`delete_email_permanently`)**: Use `email_record["account_email"]` for git commit message instead of arbitrary `get_all_accounts()[0]`.
- **backup.py (`_index_snapshot`)**: Create snapshot DB record **before** indexing emails so `first_snapshot_id` and `last_snapshot_id` are populated correctly. Pass `snapshot_id` as parameter instead of leaving it `None`.
- **backup.py (`_backup_account`)**: Clean up partial (non-empty) snapshot directories with `shutil.rmtree()` on failure, not just empty ones. Also clean up the DB record if it was already created.
- **backup.py**: Add file-based lock (`fcntl.flock` on `/tmp/zimbra_backup.lock`) to prevent concurrent backup runs from daemon and CLI.
- **rsync_handler.py**: Remove unused `estimate_rsync_size()` function.
- **docker-compose.web.yml**: Remove unused `backup_data` volume mount from web-service (it accesses files only through the API, not directly).

### Hardening

- **config.py / backup.py**: `accounts_dir()` and `_safe_email()` append an 8-character SHA-256 hash suffix to email-based directory names to prevent collisions (e.g., `a@b.c` vs `a_at_b.c`).
- **.gitignore**: Created to prevent committing `.env`, SSH keys, `__pycache__`, build artifacts, dev data, and editor configs.

## [0.2.0] - 2026-05-26

### Added

#### Pagination & Search
- **Dashboard**: Account search box + pagination (35/page). `GET /api/v1/accounts` now accepts `search`, `page`, `limit` params. `get_all_accounts()` and `count_all_accounts()` in db.py support LIKE-based search with escaping.
- **Email list**: pagination changed from 50 → 35 emails per page. Pagination links preserve all filter params (folder, search, dates, sort).
- **Test data**: `generate_test_emails.py` now creates 70 accounts with ~5620 emails total (was 3 accounts / 80 emails).

#### Email Conversion (MD / PDF / DOCX)
- **New file: `backup-service/src/converters.py`** — Converts `.eml` files to Markdown (YAML frontmatter + body), PDF (reportlab), and DOCX (python-docx).
- **API**: `GET /api/v1/emails/<id>/download?format=md|pdf|docx` for single email; `POST /api/v1/emails/bulk-download` with `{"email_ids":[...], "format":"pdf"}` for ZIP with converted files. Removed old `.eml`-based endpoints.
- **Web**: `GET /email/<id>/download/<fmt>` for direct download. Email detail page shows MD/PDF/DOCX buttons. Bulk download ZIP dropdown with format selector. "Enviar por correo" modal includes format choice.
- **Naming**: `<Subject> -restored<YYYYMMDD> -mailde<YYYYMMDD>.<ext>`
- **Dependencies added**: `reportlab==4.2.5`, `python-docx==1.1.2`

#### Sortable Columns
- **Email list**: 5 sortable columns — Fecha (`date`), De (`from_addr`), Asunto (`subject`), Carpeta (`folder`), Tamaño (`size_bytes`). Click toggles ASC/DESC, resets to page 1. Loading overlay with spinner.
- **Dashboard**: 5 sortable accounts columns — Cuenta (`email`), Dominio (`domain`), Emails (`total_emails`), Último backup (`last_backup_at`), Estado (`active`).
- **DB**: Whitelisted sort columns (`_EMAIL_SORT_COLUMNS`, `_ACCOUNT_SORT_COLUMNS`) prevent injection. `sort_by`/`sort_order` params flow through API → DB.
- **JS**: `sortBy(column)` and `accSortBy(column)` mutate URL params, preserve filters + pagination.

#### Resizable Sidebars
- **Global sidebar** (base.html): Drag handle on right edge, CSS var `--sidebar-width`, 160–600px range, persisted in `localStorage.zimbra_sidebar_width`.
- **Folder sidebar** (emails.html): Flex-based layout with `--folder-sidebar-width`, drag handle, 140–500px range, persisted in `localStorage.zimbra_folder_sidebar_width`.
- **Truncation**: Account names use `calc(var(--sidebar-width) - 72px)` instead of hardcoded `max-width: 160px`, adapting to resize.

#### Admin Configuration Panel
- **Button** in Admin → Acciones rápidas: "Configuración" opens modal with 6 tabs (General, Origen, Zimbra Remote, Offsite, Retención, Git).
- **API**: `GET /api/v1/config` returns full config as JSON. `POST /api/v1/config` saves sections to backup.conf. `POST /api/v1/config/test-ssh` tests SSH connectivity.
- **Web routes**: `/admin/config/load`, `/admin/config/save`, `/admin/config/test-ssh`. Config mount changed to `:rw` in dev compose file.
- **SSH test**: "Probar conexión" button on Zimbra Remote and Offsite tabs shows inline success/failure.

### Changed
- **docker-compose.dev.yml**: Config mount changed from `:ro` to `:rw` for admin config editing. Removed host env var interpolation in favor of hardcoded dev values.
- **Removed**: `.eml` download support from web and API. `_resend_emails_via_smtp()` function and `emails_resend` route removed.

### Fixed
- **email_detail.html**: Broken `.eml` download link (pointed to wrong port, no API key). Replaced with MD/PDF/DOCX buttons routed through web-service.
- **config modal**: Tab buttons for "Zimbra Remote" and "Offsite" had wrong `data-bs-target` IDs, causing blank content. Fixed to match JS-generated tab pane IDs.
- **Sort indicators**: HTML entities (`&#9660;`) double-escaped by Jinja2 `{{ }}`. Replaced with raw Unicode `▲`/`▼` inside `{% if %}` blocks.
- **Dashboard account name truncation**: Removed hardcoded `max-width: 160px`, replaced with CSS calc relative to `--sidebar-width`.

## [0.3.0] - 2026-05-27

### Added

#### Internationalization (English / Spanish)
- **New file: `web-service/src/i18n.py`** — Complete translation dictionary with 150+ keys for all UI strings. English is the default language; Spanish available via toggle.
- **Language detection**: `@app.before_request` reads `zimbra_lang` cookie (defaults `en`). `@app.context_processor` injects `t()` function and `lang` variable into all templates.
- **Toggle button**: `EN`/`ES` switch in navbar next to dark mode toggle. `toggleLanguage()` JS sets cookie + localStorage, triggers page reload. Persisted across sessions.
- **Translated templates**: `base.html` (sidebar, navbar), `login.html`, and `admin.html` (title, buttons, tables, config headings) fully translated. Remaining admin config modal tabs translated via Jinja2 `{{ t() }}` in template.

#### Docker Volumes → Local Bind Mounts
- All compose files (`docker-compose.yml`, `docker-compose.dev.yml`, `docker-compose.web.yml`) changed from named Docker volumes to `./data/*` bind mounts, so backup data lives alongside code and survives `docker system prune`. Added `data/*/.gitkeep` for directory structure tracking.

#### Manual de Usuario (Spanish)
- **New file: `web-service/src/manual.md`** — Complete user guide in Spanish with 7 sections (Introducción, Acceso, Dashboard, Exploración, Admin, FAQ). Renderered as styled HTML via `/manual`. Downloadable as `.md` and `.pdf`. Sidebar link for all users.
- **API**: `POST /api/v1/utils/md-to-pdf` converts arbitrary markdown to PDF using reportlab.

#### LDAP Configuration + Testing
- **Config modal**: New LDAP tab with 7 fields (host, port, bind DN, base DN, user filter, group admin DN, password). Two test buttons: "Probar conexión LDAP" (bind test) and "Probar filtro" (search for a test email to verify user filter).
- **Web routes**: `GET/POST /admin/config/auth`, `POST /admin/config/test-ldap-bind`, `POST /admin/config/test-ldap-filter`. LDAP + local users stored in `config/web.json`.
- **auth.py**: `LDAPConfig.from_web_config()` reads LDAP settings from web.json. `authenticate_demo()` reads dynamic local users from web.json instead of hardcoded dict. `admin@example.com` remains hardcoded and immutable via UI.

#### Local Users Management
- **Config modal**: New "Usuarios Locales" tab with table showing all local demo users. Add/Edit/Delete via modal form. Admin badge indicator. `admin@example.com` protected (cannot be modified via UI, only in text config).

#### Dark Mode
- Moon/Sun toggle button in the top navbar (visible to all logged-in users). Toggles `[data-theme="dark"]` on `<html>`. All UI elements adapt: cards, sidebar, tables, inputs, modals, dropdowns, pagination, alerts, badges. Persisted in `localStorage.zimbra_theme`. No auto-detection (manual only).

#### Sidebar Collapse
- Hamburger button (☰) at the far left of the navbar, before "Zimbra Backup" brand. Toggles sidebar between full width and hidden. Main content margin follows `--sidebar-width`. Persisted in `localStorage.zimbra_sidebar_collapsed`. Works identically across Dashboard, Admin, and email views.

#### Storage Row in Admin Config
- "Almacenamiento" row added to "Configuración Activa" panel showing `total_size_bytes | format_size en N snapshots`.

#### Reset Tab
- **Config modal**: New "Reset" tab with two cards:
  - **Desplegar contenido de ejemplo**: Wipes all backup data, regenerates 70 test accounts / ~5600 emails, runs initial backup.
  - **Factory Reset**: Wipes everything (snapshots, DB, git repo, test maildir) leaving a completely clean slate.
- **API**: `POST /api/v1/reset/factory` and `POST /api/v1/reset/example` with `_wipe_backup_data()` function. Resilient git re-initialization after wipe.

### Changed
- **docker-compose.dev.yml**: `dev-maildir` mount changed from `:ro` to `:rw`. Added `./scripts:/scripts:ro` mount for reset functionality.
- **config/web.json**: Added `ldap` and `local_users` sections. File permissions set to 666 for container write access.
- **`generate_test_emails.py`**: `MAILDIR_OUTPUT` env var support for custom output directory.
- **`git_handler.py`**: `_ensure_repo()` made resilient with `check=False` fallbacks for git config/commit calls after wipe.

#### Manual de Usuario v2 + PDF Rendering
- **`web-service/src/manual.md`** rewritten with complete content and updated to v0.3.0.
- **PDF rendering**: New `_md_text_to_story()` function in `converters.py` parses markdown syntax into proper reportlab elements — H1/H2/H3 headings, bold, inline code, bullet lists, tables, blockquotes, code blocks. Uses colored heading styles matching the web UI. Added `markdown==3.7` dependency.
- **Manual view**: Replaced raw HTML markdown renderer with embedded PDF via `<iframe>`. Added separate `/manual/pdf` route for inline display (no download trigger) alongside `/manual/download/pdf` for actual file download.

#### Log Viewer (Admin)
- **New sidebar entry**: "Logs del Sistema" — dark terminal-style viewer showing the backup-service log file. Color-coded log levels (gray=DEBUG, green=INFO, yellow=WARNING, red=ERROR). Selectable line count (100-2000). Refresh button.
- **Log level selector**: Dropdown to change `log_level` (DEBUG/INFO/WARNING/ERROR) directly from the log viewer. Updates `backup.conf` via config API.
- **API**: `GET /api/v1/logs?lines=200` reads last N lines from the backup log file.
- **Bug fix**: `daemon.py:setup_logging()` now uses `force=True` in `basicConfig()` so the rotating file handler attaches correctly even when the Click CLI already initialized logging.

#### Config Export / Import
- **New tab**: "Backup de Configuración" — 10th tab in Configuración del Sistema. Nav tabs now wrap to multi-row with `flex-wrap`.
- **Export**: Downloads all configurable options (backup.conf sections) as JSON. Optional password-protected ZIP with AES-256-CBC encryption via PBKDF2-HMAC-SHA256 key derivation (600K iterations). Added `cryptography==44.0.2` dependency. Warns about plain-text passwords in export.
- **Import**: File upload for `.json` or `.zip` files. Auto-detects format (plain JSON or encrypted ZIP). Wrong password returns clear error. Success message advises service restart and credential verification.
- **API**: `POST /api/v1/config/export` (returns JSON or encrypted ZIP), `POST /api/v1/config/import` (multipart file upload with optional `password` field).

#### Data Anonymization & Bootstrap
- **`config/web.json`**: Replaced real SMTP credentials with placeholders (`backup-notifications@example.com`, `CHANGE_ME`, `smtp.example.com`).
- **`.env.dev`** added to `.gitignore`.
- **`.gitkeep`** files in `config/ssh/` and all `data/*` subdirectories for directory tracking through git.
- **Makefile** `setup` and `dev-up` targets create and `chmod 777` data directories before Docker starts.
- **Bootstrap verified**: clean clone → `make dev-up` → services healthy, backup runs, web accessible.

### Changed
- **Sidebar**: Removed redundant accounts list (already available in main Dashboard table with search, pagination, and sorting).
- **`.gitignore`**: `data/*` excluded, `!data/*/.gitkeep` allowed. `.env.dev` excluded.

### Fixed
- **Dark mode text**: `fw-semibold`, alerts, badges, and border colors now properly inherit CSS variables in dark mode.
- **Config modal**: Upgraded from `modal-lg` to `modal-xl` with `max-height: 90vh`. Local users action buttons aligned with `text-nowrap` and wider column.
- **`web.json` write permission**: Set to 666 so `webuser` container can write via auth config save route.
- **`data/` permissions**: Set to 777 so `backupd` container can write to bind-mounted data directories.
- **Reset git crash**: `_wipe_backup_data()` now re-initializes git via `GitHandler()` after wipe instead of leaving empty directory.
- **Git ownership error**: Added `git config --global --add safe.directory /data/git` in `entrypoint.sh` and `git_handler.py:_ensure_repo()`. The Dockerfile's `RUN git config --global` ran as `root` instead of `backupd`, so the safe.directory setting didn't apply to the runtime user.
- **Manual PDF auto-download**: Added separate `/manual/pdf` inline route for iframe embedding vs `/manual/download/pdf` for download.