# Zimbra Backup System

Automated backup system for Zimbra 9 / Postfix mailboxes (Maildir format) with GFS retention, rsync hardlinks, web UI with LDAP authentication, and Docker Compose.

## Features

- **Automated backup** every 2 hours (configurable), with APScheduler
- **Efficient snapshots** using `rsync --link-dest` hardlinks — unchanged emails use zero extra space
- **GFS retention** (Grandfather-Father-Son): hourly, daily, weekly, monthly — configurable for decades
- **Remote pull-mode**: backup server extracts mailboxes from Zimbra via SSH/rsync — no agents required on Zimbra
- **Web interface** (Flask + Bootstrap 5) with LDAP authentication and demo mode
- **Email export**: individual or bulk download in **Markdown**, **PDF**, and **DOCX** formats, with password-protected ZIP export
- **Role-based access**: users see their own emails; admins access all accounts and manage configuration
- **Config backup**: export/import full system configuration with AES-256-CBC encryption
- **Dark mode** and **English/Spanish** internationalization
- **CLI tool** (`zbackup`) with status, listing, backup, prune, and delete commands
- **SQLite** with WAL mode for concurrent reads
- **Git** versioning for metadata manifests
- **Docker Compose** with persistent bind-mounted data volumes

## Quick Start (Development)

```bash
git clone <repo-url> zimbra-backup
cd zimbra-backup
make setup
make dev-up
# Web: http://localhost:8080
# Login: admin@example.com / admin123
```

The dev environment uses local Maildir test data (70 accounts, ~5600 emails) and demo authentication. No Zimbra server needed.

## Quick Start (Production)

```bash
# On the BACKUP server (NOT the Zimbra server)
make setup
bash scripts/setup-ssh-key.sh
# Copy key to Zimbra server and configure
make up-all
```

### Prerequisites

| Component | Minimum |
|-----------|---------|
| Docker Engine | 24+ |
| Docker Compose | v2.x |
| SSH access to Zimbra server | Port 22 |
| Disk space | Proportional to mailstore (hardlinks minimize usage) |

## Architecture

```
[Zimbra Server]                      [Backup Server]
  /opt/zimbra/store/                  docker compose up
  zmprov gaa              ←── SSH ──  backup-service (:8001)
                           ←─ rsync ─  · Snapshots with hardlinks
                                       · SQLite DB + git metadata

                                       web-service (:8080)
                                       · LDAP auth
                                       · Browse, search, export emails
```

## Project Structure

```
backup-zimbra/
├── backup-service/src/              # Python daemon + CLI + API REST
│   ├── config.py                    # BackupConfig, load/save
│   ├── db.py                        # SQLite, WAL mode, pagination
│   ├── backup.py                    # BackupEngine — discover, rsync, index
│   ├── retention.py                 # GFS policy
│   ├── rsync_handler.py             # rsync_local, rsync_from_zimbra
│   ├── maildir.py                   # Email parsing and scanning
│   ├── converters.py                # Email → MD, PDF, DOCX
│   ├── git_handler.py               # Git metadata versioning
│   ├── api.py                       # Flask REST API
│   ├── daemon.py                    # APScheduler
│   └── cli.py                       # Click CLI (zbackup)
├── web-service/src/                 # Flask web UI
│   ├── app.py                       # Routes, auth, download proxies
│   ├── auth.py                      # LDAP + demo authentication
│   ├── api_client.py                # HTTP client → backup-service
│   ├── i18n.py                      # EN/ES translations (150+ keys)
│   └── templates/                   # Bootstrap 5 templates
├── config/                          # backup.conf, web.json, ssh keys
├── data/                            # Persistent data (bind mounts)
├── scripts/                         # generate_test_emails.py, setup-ssh-key.sh
├── docker-compose.yml               # Production
├── docker-compose.dev.yml           # Development
└── Makefile
```

## CLI Commands

```bash
zbackup status              # System overview
zbackup list-accounts       # All accounts with stats
zbackup list-emails <email> # Emails for account
zbackup list-backups [email] # Available snapshots
zbackup backup              # Trigger manual backup
zbackup prune [--dry-run]   # Apply retention policy
zbackup delete-email <id> --confirm  # Permanently delete email
zbackup daemon              # Start scheduler daemon
zbackup init                # Initialize system
```

## Web Interface

| Role | Capabilities |
|------|--------------|
| **User** | View own emails, search/filter, export MD/PDF/DOCX, download ZIP, send via email |
| **Admin** | All of the above + all accounts, delete emails, admin panel, full system configuration, log viewer |

### Key Admin Features
- **Dashboard**: searchable, paginated accounts list (35/page) with sortable columns
- **Email list**: paginated (35/page), sortable, multi-select export, send by email
- **Config panel**: 10-tab modal — General, Source, Zimbra Remote, Offsite, Retention, Git, LDAP, Local Users, Reset, Config Backup
- **Log viewer**: color-coded terminal-style log with live level change
- **User manual**: embedded PDF viewer with download options
- **Dark mode**: manual toggle, persisted per-user
- **Language**: English / Spanish toggle, persisted per-user
- **Resizable sidebars**: drag handles, localStorage persistence
- **Config export/import**: JSON or AES-256-CBC encrypted ZIP with password protection

## Retention Policy (GFS)

```ini
[retention]
hourly_keep  = 48    # ~4 days
daily_keep   = 30    # ~1 month
weekly_keep  = 52    # ~1 year
monthly_keep = 120   # ~10 years
```

**Storage efficiency**: hardlinks via `rsync --link-dest` mean unchanged emails occupy space only once across all snapshots.

## Configuration

All configuration is editable through the web admin panel.

| File | Purpose |
|------|---------|
| `config/backup.conf` | Backup service settings (paths, intervals, retention, SSH) |
| `config/web.json` | SMTP, LDAP, and local demo users |
| `.env` | Environment variables (API keys, hostnames) — gitignored |

## Development

```bash
make dev-up          # build + start services
make dev-backup      # run manual backup
make dev-status      # CLI status
make dev-logs        # tail logs
make dev-shell       # shell in backup container

# Rebuild after code changes
sudo docker compose -f docker-compose.dev.yml up -d --build
```

Demo users: `admin@example.com` / `admin123` (admin), `user1@example.com` / `user123`, `user2@example.com` / `user123`.

## License

MIT — see [LICENSE](LICENSE).

## Security

- API key authentication for internal REST calls
- CSRF protection on all POST forms
- LDAP credentials via environment variables (never committed)
- SSH keys stored with `chmod 600`
- Demo auth mode for development only
- SQL whitelisted column names prevent injection
- LIKE search inputs escaped with `ESCAPE '\'`
- Config export encryption via AES-256-CBC with PBKDF2-HMAC-SHA256
