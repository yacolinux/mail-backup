"""API REST interna del backup-service.

Expone endpoints para que el web-service consulte datos de backup
y ejecute operaciones (listar, eliminar emails, disparar backup).
Protegida con API key simple en header X-API-Key.
"""

import io
import json
import logging
import os
from datetime import datetime, timezone
from functools import wraps
from typing import Any, Dict

from flask import Flask, jsonify, request, abort, send_file

logger = logging.getLogger(__name__)
UTC = timezone.utc

BULK_MAX_EMAILS = 200


def _require_api_key(f):
    """Decorator: verifica X-API-Key en headers."""
    @wraps(f)
    def decorated(*args, **kwargs):
        from flask import current_app
        key = request.headers.get("X-API-Key", "")
        config = current_app.config.get("BACKUP_CONFIG")
        expected = config.api_key if config else os.environ.get("BACKUP_API_KEY", "")
        if not key or key != expected:
            abort(401, description="API key inválida o ausente")
        return f(*args, **kwargs)
    return decorated


def _ok(data: Any, status: int = 200):
    return jsonify({"ok": True, "data": data}), status


def _err(message: str, status: int = 400):
    return jsonify({"ok": False, "error": message}), status


def create_app(config=None, db=None):
    """Fábrica de la aplicación Flask con inyección de config y DB."""
    app = Flask(__name__)
    app.config["BACKUP_CONFIG"] = config
    app.config["BACKUP_DB"] = db

    # =====================================================================
    # Health
    # =====================================================================

    @app.route("/api/v1/health")
    def health():
        return jsonify({
            "status": "ok",
            "service": "zimbra-backup-service",
            "timestamp": datetime.now(UTC).isoformat(),
        })

    # =====================================================================
    # Status
    # =====================================================================

    @app.route("/api/v1/status")
    @_require_api_key
    def get_status():
        cfg = app.config["BACKUP_CONFIG"]
        ddb = app.config["BACKUP_DB"]
        stats = ddb.get_system_stats()
        runs = ddb.get_recent_runs(5)
        return _ok({
            "stats": stats,
            "recent_runs": runs,
            "config": {
                "backup_interval_hours": cfg.backup_interval_hours,
                "remote_enabled": cfg.remote_enabled,
                "git_enabled": cfg.git_enabled,
                "retention": {
                    "hourly_keep": cfg.retention.hourly_keep,
                    "daily_keep": cfg.retention.daily_keep,
                    "weekly_keep": cfg.retention.weekly_keep,
                    "monthly_keep": cfg.retention.monthly_keep,
                },
            },
        })

    # =====================================================================
    # Accounts
    # =====================================================================

    @app.route("/api/v1/accounts")
    @_require_api_key
    def list_accounts():
        ddb = app.config["BACKUP_DB"]
        search = request.args.get("search")
        page = int(request.args.get("page", 1))
        limit = min(int(request.args.get("limit", 35)), 200)
        offset = (page - 1) * limit
        sort_by = request.args.get("sort", "email")
        sort_order = request.args.get("order", "ASC")
        accounts = ddb.get_all_accounts(search=search, limit=limit, offset=offset,
                                        sort_by=sort_by, sort_order=sort_order)
        total = ddb.count_all_accounts(search=search)
        return _ok({"accounts": accounts, "total": total, "page": page, "limit": limit})

    @app.route("/api/v1/accounts/<path:email>")
    @_require_api_key
    def get_account(email):
        ddb = app.config["BACKUP_DB"]
        acc = ddb.get_account_by_email(email)
        if not acc:
            return _err(f"Cuenta no encontrada: {email}", 404)
        folders = ddb.get_account_folders(acc["id"])
        snapshots = ddb.get_account_snapshots(acc["id"], limit=10)
        return _ok({**acc, "folders": folders, "recent_snapshots": snapshots})

    # =====================================================================
    # Emails
    # =====================================================================

    @app.route("/api/v1/accounts/<path:email>/emails")
    @_require_api_key
    def list_emails(email):
        ddb = app.config["BACKUP_DB"]
        acc = ddb.get_account_by_email(email)
        if not acc:
            return _err(f"Cuenta no encontrada: {email}", 404)

        folder = request.args.get("folder")
        search = request.args.get("search")
        date_from = request.args.get("date_from")
        date_to = request.args.get("date_to")
        include_deleted = request.args.get("deleted", "false").lower() == "true"
        limit = min(int(request.args.get("limit", 50)), 500)
        offset = int(request.args.get("offset", 0))
        sort_by = request.args.get("sort", "date")
        sort_order = request.args.get("order", "DESC")

        emails = ddb.get_account_emails(
            account_id=acc["id"],
            folder=folder,
            search=search,
            date_from=date_from,
            date_to=date_to,
            include_deleted=include_deleted,
            limit=limit,
            offset=offset,
            sort_by=sort_by,
            sort_order=sort_order,
        )
        total = ddb.count_account_emails_filtered(
            acc["id"], folder=folder, search=search, include_deleted=include_deleted
        )
        return _ok({"emails": emails, "total": total, "limit": limit, "offset": offset})

    @app.route("/api/v1/emails/<int:email_id>")
    @_require_api_key
    def get_email(email_id):
        ddb = app.config["BACKUP_DB"]
        email_record = ddb.get_email_by_id(email_id)
        if not email_record:
            return _err(f"Email {email_id} no encontrado", 404)
        return _ok(email_record)

    @app.route("/api/v1/emails/<int:email_id>/content")
    @_require_api_key
    def get_email_content(email_id):
        """Retorna el contenido completo de un email (headers + body)."""
        from .maildir import find_email_file, get_email_content as parse_content

        ddb = app.config["BACKUP_DB"]
        email_record = ddb.get_email_by_id(email_id)
        if not email_record:
            return _err(f"Email {email_id} no encontrado", 404)

        if email_record.get("deleted"):
            return _err("Email eliminado", 410)

        acc_id = email_record["account_id"]
        filename = email_record["filename"]
        snapshots = ddb.get_account_snapshots(acc_id, limit=50)

        filepath = None
        for snap in snapshots:
            snap_path = snap.get("snapshot_path", "")
            if snap_path and os.path.exists(snap_path):
                f = find_email_file(snap_path, filename)
                if f:
                    filepath = f
                    break

        if not filepath:
            return _err("Archivo de email no encontrado en ningún snapshot", 404)

        content = parse_content(filepath)
        if not content:
            return _err("Error leyendo email", 500)

        return _ok(content)

    @app.route("/api/v1/emails/<int:email_id>/download")
    @_require_api_key
    def get_email_download(email_id):
        """Descarga un email convertido a MD, PDF o DOCX."""
        from .maildir import find_email_file
        from .converters import convert_email

        fmt = request.args.get("format", "md").lower()
        if fmt not in ("md", "pdf", "docx"):
            return _err("Formato inválido. Usar: md, pdf, docx", 400)

        ddb = app.config["BACKUP_DB"]
        email_record = ddb.get_email_by_id(email_id)
        if not email_record:
            return _err(f"Email {email_id} no encontrado", 404)

        if email_record.get("deleted"):
            return _err("Email eliminado", 410)

        acc_id = email_record["account_id"]
        filename = email_record["filename"]
        snapshots = ddb.get_account_snapshots(acc_id, limit=50)

        filepath = None
        for snap in snapshots:
            snap_path = snap.get("snapshot_path", "")
            if snap_path and os.path.exists(snap_path):
                f = find_email_file(snap_path, filename)
                if f:
                    filepath = f
                    break

        if not filepath:
            return _err("Archivo no encontrado", 404)

        result = convert_email(filepath, fmt)
        if result is None:
            return _err(f"Error convirtiendo email a {fmt}", 500)

        data, mimetype, download_name = result
        return send_file(
            io.BytesIO(data),
            mimetype=mimetype,
            as_attachment=True,
            download_name=download_name,
        )

    @app.route("/api/v1/emails/bulk-download", methods=["POST"])
    @_require_api_key
    def bulk_download_emails():
        """Empaqueta múltiples emails convertidos en un ZIP."""
        import zipfile as zf_mod
        from .maildir import find_email_file
        from .converters import convert_email

        ddb = app.config["BACKUP_DB"]

        data = request.get_json(silent=True) or {}
        email_ids = data.get("email_ids", [])
        fmt = (data.get("format") or "md").lower()

        if not isinstance(email_ids, list) or not email_ids:
            return _err("Se requiere 'email_ids' como lista no vacía", 400)
        if fmt not in ("md", "pdf", "docx"):
            return _err("Formato inválido. Usar: md, pdf, docx", 400)
        if len(email_ids) > BULK_MAX_EMAILS:
            return _err(f"Máximo {BULK_MAX_EMAILS} emails por operación", 400)

        buf = io.BytesIO()
        found = 0
        with zf_mod.ZipFile(buf, "w", zf_mod.ZIP_DEFLATED) as zf:
            for email_id in email_ids:
                try:
                    email_record = ddb.get_email_by_id(int(email_id))
                except (TypeError, ValueError):
                    continue
                if not email_record or email_record.get("deleted"):
                    continue

                acc_id = email_record["account_id"]
                filename = email_record["filename"]
                snapshots = ddb.get_account_snapshots(acc_id, limit=50)

                for snap in snapshots:
                    snap_path = snap.get("snapshot_path", "")
                    if snap_path and os.path.exists(snap_path):
                        filepath = find_email_file(snap_path, filename)
                        if filepath:
                            result = convert_email(filepath, fmt)
                            if result:
                                file_data, _, arc_name = result
                                zf.writestr(arc_name, file_data)
                                found += 1
                            break

        if found == 0:
            return _err("No se encontraron archivos para los IDs indicados", 404)

        buf.seek(0)
        ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        return send_file(
            buf,
            mimetype="application/zip",
            as_attachment=True,
            download_name=f"emails_backup_{ts}.zip",
        )

    @app.route("/api/v1/emails/<int:email_id>", methods=["DELETE"])
    @_require_api_key
    def delete_email(email_id):
        """Elimina permanentemente un email de todos los snapshots."""
        from .backup import BackupEngine
        cfg = app.config["BACKUP_CONFIG"]
        ddb = app.config["BACKUP_DB"]
        engine = BackupEngine(cfg, ddb)
        try:
            result = engine.delete_email_permanently(email_id)
            return _ok(result)
        except ValueError as e:
            return _err(str(e), 404)
        except Exception as e:
            logger.error(f"Error eliminando email {email_id}: {e}", exc_info=True)
            return _err(str(e), 500)

    # =====================================================================
    # Snapshots
    # =====================================================================

    @app.route("/api/v1/accounts/<path:email>/snapshots")
    @_require_api_key
    def list_snapshots(email):
        ddb = app.config["BACKUP_DB"]
        acc = ddb.get_account_by_email(email)
        if not acc:
            return _err(f"Cuenta no encontrada: {email}", 404)
        snapshots = ddb.get_account_snapshots(acc["id"])
        return _ok(snapshots)

    # =====================================================================
    # Backup triggers
    # =====================================================================

    @app.route("/api/v1/backup/run", methods=["POST"])
    @_require_api_key
    def trigger_backup():
        """Dispara un backup inmediato."""
        from .backup import BackupEngine
        cfg = app.config["BACKUP_CONFIG"]
        ddb = app.config["BACKUP_DB"]
        engine = BackupEngine(cfg, ddb)
        try:
            result = engine.run(trigger="manual")
            return _ok(result)
        except Exception as e:
            return _err(str(e), 500)

    @app.route("/api/v1/retention/apply", methods=["POST"])
    @_require_api_key
    def apply_retention():
        """Aplica la política de retención ahora."""
        from .retention import RetentionManager
        cfg = app.config["BACKUP_CONFIG"]
        ddb = app.config["BACKUP_DB"]
        retention = RetentionManager(cfg, ddb)
        result = retention.apply_all()
        return _ok(result)

    # =====================================================================
    # Git log
    # =====================================================================

    @app.route("/api/v1/git/log")
    @_require_api_key
    def git_log_view():
        cfg = app.config["BACKUP_CONFIG"]
        if not cfg.git_enabled:
            return _err("Git no habilitado en configuración", 404)
        from .git_handler import GitHandler
        git = GitHandler(cfg)
        log = git.get_log(20)
        return _ok(log)

    # =====================================================================
    # Configuration
    # =====================================================================

    @app.route("/api/v1/config", methods=["GET", "POST"])
    @_require_api_key
    def config_endpoint():
        """Lee o actualiza la configuración."""
        from .config import config_to_dict, save_config

        cfg = app.config["BACKUP_CONFIG"]
        config_path = os.environ.get("BACKUP_CONFIG", "/config/backup.conf")

        if request.method == "GET":
            return _ok(config_to_dict(cfg))

        data = request.get_json(silent=True) or {}
        try:
            save_config(config_path, data)
            # Reload config so changes take effect
            from .config import load_config
            new_cfg = load_config(config_path)
            app.config["BACKUP_CONFIG"] = new_cfg
            return _ok({"message": "Configuración guardada correctamente",
                        "config": config_to_dict(new_cfg)})
        except Exception as e:
            return _err(f"Error guardando configuración: {e}", 500)

    @app.route("/api/v1/config/test-ssh", methods=["POST"])
    @_require_api_key
    def test_ssh_connection():
        """Prueba la conexión SSH al servidor Zimbra."""
        import subprocess

        data = request.get_json(silent=True) or {}
        host = data.get("host", "")
        user = data.get("user", "")
        ssh_key = data.get("ssh_key", "/config/ssh/id_rsa")
        ssh_port = str(data.get("ssh_port", 22))

        if not host or not user:
            return _err("Se requiere host y usuario SSH", 400)

        try:
            result = subprocess.run(
                ["ssh", "-i", ssh_key, "-p", ssh_port,
                 "-o", "StrictHostKeyChecking=accept-new",
                 "-o", "ConnectTimeout=10",
                 "-o", "BatchMode=yes",
                 f"{user}@{host}", "echo SSH_OK"],
                capture_output=True, text=True, timeout=15,
            )
            if result.returncode == 0 and "SSH_OK" in result.stdout:
                return _ok({"success": True, "message": f"Conectado a {host} via SSH"})
            else:
                return _ok({"success": False,
                            "message": result.stderr.strip() or "Error de conexión"})
        except subprocess.TimeoutExpired:
            return _ok({"success": False, "message": "Timeout: el servidor no responde"})
        except Exception as e:
            return _ok({"success": False, "message": str(e)})

    @app.route("/api/v1/logs")
    @_require_api_key
    def get_logs():
        """Retorna las últimas líneas del archivo de log."""
        cfg = app.config["BACKUP_CONFIG"]
        lines = min(int(request.args.get("lines", 200)), 2000)
        try:
            import os as _os
            if not _os.path.exists(cfg.log_path):
                return _ok({"content": "", "lines": 0, "message": "Archivo de log no encontrado"})
            with open(cfg.log_path, "r", encoding="utf-8", errors="replace") as f:
                data = f.read()
            log_lines = data.split("\n")
            if len(log_lines) > lines:
                log_lines = log_lines[-lines:]
            return _ok({
                "content": "\n".join(log_lines),
                "lines": len(log_lines),
                "total_size": len(data),
                "log_level": cfg.log_level,
            })
        except Exception as e:
            return _err(f"Error leyendo log: {e}", 500)

    @app.route("/api/v1/config/export", methods=["POST"])
    @_require_api_key
    def config_export():
        """Exporta toda la configuración como JSON, opcionalmente en ZIP cifrado."""
        import zipfile as zf_mod
        from .config import config_to_dict

        data = request.get_json(silent=True) or {}
        password = data.get("password", "").strip()

        # Build full config payload
        cfg = app.config["BACKUP_CONFIG"]
        payload = config_to_dict(cfg)
        payload["_meta"] = {
            "exported_at": datetime.now(UTC).isoformat(),
            "version": "0.3.0",
            "type": "zimbra-backup-config",
        }

        json_data = json.dumps(payload, indent=2, ensure_ascii=False).encode("utf-8")
        ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")

        if password:
            try:
                from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
                from cryptography.hazmat.primitives import hashes
                from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
                from cryptography.hazmat.backends import default_backend
                import os as _os

                salt = _os.urandom(16)
                iv = _os.urandom(16)
                kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=600000)
                key = kdf.derive(password.encode("utf-8"))
                cipher = Cipher(algorithms.AES(key), modes.CBC(iv), backend=default_backend())
                encryptor = cipher.encryptor()
                padded = json_data + b"\x00" * (16 - len(json_data) % 16)
                encrypted = encryptor.update(padded) + encryptor.finalize()

                buf = io.BytesIO()
                with zf_mod.ZipFile(buf, "w", zf_mod.ZIP_DEFLATED) as zf:
                    zf.writestr("salt.bin", salt)
                    zf.writestr("iv.bin", iv)
                    zf.writestr("config.json.enc", encrypted)
                    zf.writestr("README.txt", (
                        "Archivo de configuración cifrado de Zimbra Backup System.\n"
                        "Para restaurar, use la opción 'Importar' con la misma contraseña.\n"
                        "Exportado: " + datetime.now(UTC).isoformat() + "\n"
                    ).encode("utf-8"))
                buf.seek(0)
                return send_file(buf, mimetype="application/zip",
                                 as_attachment=True,
                                 download_name=f"zimbra_backup_config_{ts}.zip")
            except ImportError:
                return _err("Librería cryptography no instalada", 500)

        return send_file(io.BytesIO(json_data), mimetype="application/json",
                         as_attachment=True,
                         download_name=f"zimbra_backup_config_{ts}.json")

    @app.route("/api/v1/config/import", methods=["POST"])
    @_require_api_key
    def config_import():
        """Importa configuración desde JSON o ZIP cifrado."""
        import zipfile as zf_mod
        from .config import save_config, config_to_dict

        if "file" not in request.files:
            return _err("Se requiere un archivo en el campo 'file'", 400)

        file = request.files["file"]
        raw = file.read()
        password = request.form.get("password", "").strip()
        payload = None

        # Try JSON first
        try:
            payload = json.loads(raw.decode("utf-8"))
            if payload.get("_meta", {}).get("type") != "zimbra-backup-config":
                payload = None
        except Exception:
            pass

        # Try encrypted ZIP
        if payload is None and raw[:2] == b"PK":
            try:
                buf = io.BytesIO(raw)
                with zf_mod.ZipFile(buf, "r") as zf:
                    names = zf.namelist()
                    if "config.json.enc" in names and password:
                        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
                        from cryptography.hazmat.primitives import hashes
                        from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
                        from cryptography.hazmat.backends import default_backend

                        salt = zf.read("salt.bin")
                        iv = zf.read("iv.bin")
                        encrypted = zf.read("config.json.enc")
                        kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=600000)
                        key = kdf.derive(password.encode("utf-8"))
                        cipher = Cipher(algorithms.AES(key), modes.CBC(iv), backend=default_backend())
                        decryptor = cipher.decryptor()
                        decrypted = decryptor.update(encrypted) + decryptor.finalize()
                        decrypted = decrypted.rstrip(b"\x00")
                        payload = json.loads(decrypted.decode("utf-8"))
                    elif "config.json" in names:
                        payload = json.loads(zf.read("config.json").decode("utf-8"))
            except Exception as e:
                return _err(f"Error leyendo ZIP: {e}", 400)

        if not payload or payload.get("_meta", {}).get("type") != "zimbra-backup-config":
            return _err("Formato de archivo no reconocido. Use un .json o .zip exportado por esta aplicación.", 400)

        # Apply config
        try:
            config_path = os.environ.get("BACKUP_CONFIG", "/config/backup.conf")
            # Remove _meta before saving
            clean = {k: v for k, v in payload.items() if k != "_meta"}
            save_config(config_path, clean)
            # Reload in-memory config
            from .config import load_config
            new_cfg = load_config(config_path)
            app.config["BACKUP_CONFIG"] = new_cfg
            return _ok({"message": "Configuración importada correctamente. Reinicie el servicio para aplicar todos los cambios."})
        except Exception as e:
            return _err(f"Error aplicando configuración: {e}", 500)

    @app.route("/api/v1/utils/md-to-pdf", methods=["POST"])
    @_require_api_key
    def md_to_pdf():
        """Convierte texto markdown a PDF con formato."""
        import io as _io
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import cm
        from reportlab.platypus import SimpleDocTemplate, Paragraph
        from .converters import _md_text_to_story

        data = request.get_json(silent=True) or {}
        text = data.get("markdown", "")
        title = data.get("title", "Documento")
        if not text:
            return _err("Se requiere 'markdown' en el cuerpo", 400)

        buf = _io.BytesIO()
        doc = SimpleDocTemplate(
            buf, pagesize=A4,
            leftMargin=2*cm, rightMargin=2*cm,
            topMargin=2*cm, bottomMargin=2*cm,
            title=title,
        )
        styles = getSampleStyleSheet()
        title_style = ParagraphStyle("DocTitle", parent=styles["Title"], fontSize=20, leading=26, spaceAfter=16)
        story = [Paragraph(title, title_style)]
        story.extend(_md_text_to_story(text, styles))
        doc.build(story)

        buf.seek(0)
        safe_title = "".join(c if c.isalnum() or c in " -_." else "_" for c in title)[:50]
        return send_file(buf, mimetype="application/pdf", as_attachment=True,
                         download_name=f"{safe_title}.pdf")

    # =====================================================================
    # Reset / Wipe
    # =====================================================================

    def _wipe_backup_data():
        """Borra todos los datos de backup: snapshots, DB, git repo."""
        import shutil
        import os as _os

        backup_dir = app.config["BACKUP_CONFIG"].backup_dir
        db_path = app.config["BACKUP_CONFIG"].db_path
        git_path = app.config["BACKUP_CONFIG"].git.repo_path

        # Delete snapshot data
        if _os.path.exists(backup_dir):
            shutil.rmtree(backup_dir, ignore_errors=True)
            _os.makedirs(backup_dir, exist_ok=True)

        # Delete DB and WAL files, re-init
        db_dir = _os.path.dirname(db_path)
        for fname in list(_os.listdir(db_dir)) if _os.path.exists(db_dir) else []:
            if "backup" in fname:
                try:
                    _os.unlink(_os.path.join(db_dir, fname))
                except OSError:
                    pass
        ddb = app.config["BACKUP_DB"]
        ddb._init_schema()

        # Wipe git repo — completely remove and recreate
        if _os.path.exists(git_path):
            shutil.rmtree(git_path, ignore_errors=True)
        _os.makedirs(git_path, exist_ok=True)
        # Re-init git so subsequent BackupEngine works
        from .git_handler import GitHandler
        GitHandler(app.config["BACKUP_CONFIG"])

    @app.route("/api/v1/reset/factory", methods=["POST"])
    @_require_api_key
    def factory_reset():
        """Factory reset: borra todos los datos de backup, deja el sistema limpio."""
        from pathlib import Path as _Path
        import shutil

        _wipe_backup_data()

        # Also wipe test maildir if it exists
        maildir_base = app.config["BACKUP_CONFIG"].maildir_base
        maildir_path = _Path(maildir_base)
        if maildir_path.exists():
            shutil.rmtree(maildir_path, ignore_errors=True)

        return _ok({"message": "Factory reset completado. Sistema limpio listo para comenzar."})

    @app.route("/api/v1/reset/example", methods=["POST"])
    @_require_api_key
    def example_reset():
        """Deploy example: borra todo, regenera datos de prueba, ejecuta backup."""
        import subprocess, sys
        from pathlib import Path as _Path
        import os as _os

        _wipe_backup_data()

        # Clear existing maildir first, then set correct output path
        maildir_base = app.config["BACKUP_CONFIG"].maildir_base
        import shutil
        maildir_p = _Path(maildir_base)
        if maildir_p.exists():
            shutil.rmtree(maildir_p, ignore_errors=True)

        # Run the test email generator
        script_path = "/scripts/generate_test_emails.py"
        output = ""
        if _Path(script_path).exists():
            env = _os.environ.copy()
            env["MAILDIR_OUTPUT"] = maildir_base
            try:
                result = subprocess.run(
                    [sys.executable, script_path],
                    capture_output=True, text=True, timeout=60,
                    env=env,
                )
                output = result.stdout[-500:] + "\n" + result.stderr[-200:]
                if result.returncode != 0:
                    return _err(f"Error generando emails de prueba:\n{output}", 500)
            except Exception as e:
                return _err(f"Error ejecutando script: {e}", 500)
        else:
            return _err("Script generate_test_emails.py no encontrado en /scripts", 500)

        # Run initial backup
        from .backup import BackupEngine
        engine = BackupEngine(app.config["BACKUP_CONFIG"], app.config["BACKUP_DB"])
        try:
            result = engine.run(trigger="manual")
            return _ok({
                "message": "Contenido de ejemplo desplegado y backup ejecutado.",
                "backup": result,
                "script_output": output.split("\n") if output else [],
            })
        except Exception as e:
            return _err(f"Datos generados pero backup falló: {e}", 500)

    return app


def run_api(config, db, host: str = "0.0.0.0", port: int = 8001):
    """Inicia el servidor de API REST."""
    application = create_app(config, db)
    logger.info(f"Iniciando API REST en {host}:{port}")
    application.run(host=host, port=port, debug=False, use_reloader=False, threaded=True)