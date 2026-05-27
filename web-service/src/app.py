"""Interfaz web del sistema de backup Zimbra - Fase 2.

Flask app con:
- Auth LDAP (login/logout)
- Dashboard con estado del sistema
- Listado de emails por cuenta/carpeta
- Vista de email individual
- Eliminación de emails (con confirmación)
- Panel de admin (para usuarios con rol admin)
"""

import io
import json
import logging
import os
import secrets
import smtplib
from datetime import datetime
from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from functools import wraps
from typing import Optional

from flask import (
    Flask,
    abort,
    flash,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)

from .api_client import BackupAPIClient
from .auth import LDAPConfig, AuthUser, authenticate, authenticate_demo
from .i18n import t, t_fmt

logger = logging.getLogger(__name__)

app = Flask(__name__, template_folder="templates")
app.secret_key = os.environ.get("WEB_SECRET_KEY", "dev-secret-change-in-production")
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["PERMANENT_SESSION_LIFETIME"] = 3600 * 8  # 8 horas

USE_DEMO_AUTH = os.environ.get("DEMO_AUTH", "false").lower() == "true"


# =============================================================================
# Language detection
# =============================================================================

@app.before_request
def detect_language():
    lang = request.cookies.get("zimbra_lang", "en")
    if lang not in ("en", "es"):
        lang = "en"
    g.lang = lang

@app.context_processor
def inject_translations():
    lang = getattr(g, "lang", "en")
    return {"t": lambda key, **kw: t_fmt(key, lang, **kw) if kw else t(key, lang),
            "lang": lang,
            "T": t}


# =============================================================================
# CSRF
# =============================================================================

def generate_csrf_token():
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_hex(32)
    return session["csrf_token"]


@app.before_request
def check_csrf():
    if request.method == "POST" and request.endpoint and request.endpoint != "login":
        token = request.form.get("csrf_token", "")
        if token != session.get("csrf_token", ""):
            abort(403)


app.jinja_env.globals["csrf_token"] = generate_csrf_token

# =============================================================================
# Helpers de sesión y auth
# =============================================================================

def _current_user() -> Optional[AuthUser]:
    if "user_email" not in session:
        return None
    return AuthUser(
        email=session["user_email"],
        display_name=session.get("user_display_name", session["user_email"]),
        is_admin=session.get("user_is_admin", False),
    )


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        user = _current_user()
        if not user:
            return redirect(url_for("login", next=request.url))
        g.user = user
        return f(*args, **kwargs)
    return decorated


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        user = _current_user()
        if not user:
            return redirect(url_for("login"))
        if not user.is_admin:
            abort(403)
        g.user = user
        return f(*args, **kwargs)
    return decorated


def _get_api() -> BackupAPIClient:
    return BackupAPIClient()


def _format_size(bytes_: int) -> str:
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if bytes_ < 1024:
            return f"{bytes_:.1f} {unit}"
        bytes_ /= 1024
    return f"{bytes_:.1f} PB"


def _format_date(iso_str: Optional[str]) -> str:
    if not iso_str:
        return "-"
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        return dt.strftime("%d/%m/%Y %H:%M")
    except Exception:
        return iso_str[:16] if iso_str else "-"


# Registrar filtros en Jinja2
app.jinja_env.filters["format_size"] = _format_size
app.jinja_env.filters["format_date"] = _format_date


def _load_web_config() -> dict:
    """Carga la configuración JSON del web-service."""
    config_path = os.environ.get("WEB_CONFIG", "/config/web.json")
    try:
        with open(config_path) as f:
            return json.load(f)
    except FileNotFoundError:
        logger.warning(f"Config web no encontrado: {config_path}")
        return {}
    except Exception as e:
        logger.error(f"Error cargando config web: {e}")
        return {}


def _send_zip_via_smtp(zip_bytes: bytes, to_addr: str, email_count: int, smtp_cfg: dict, fmt_label: str = "MD") -> None:
    """Envía los emails backupeados como ZIP adjunto via SMTP."""
    from_addr = smtp_cfg.get("from_addr") or smtp_cfg.get("username") or "backup@localhost"
    msg = MIMEMultipart()
    msg["From"] = from_addr
    msg["To"] = to_addr
    msg["Subject"] = f"Zimbra Backup - {email_count} email(s) exportados ({fmt_label})"
    msg.attach(MIMEText(
        f"Se adjuntan {email_count} email(s) exportados desde el sistema de backup de Zimbra en formato {fmt_label}."
    ))
    attachment = MIMEBase("application", "zip")
    attachment.set_payload(zip_bytes)
    encoders.encode_base64(attachment)
    attachment.add_header("Content-Disposition", "attachment", filename="emails_backup.zip")
    msg.attach(attachment)

    host = smtp_cfg.get("host", "localhost")
    port = int(smtp_cfg.get("port", 25))
    with smtplib.SMTP(host, port, timeout=30) as server:
        if smtp_cfg.get("use_tls"):
            server.starttls()
        username = smtp_cfg.get("username")
        password = smtp_cfg.get("password")
        if username and password:
            server.login(username, password)
        server.sendmail(from_addr, [to_addr], msg.as_bytes())


# =============================================================================
# Health check
# =============================================================================

@app.route("/health")
def health():
    return jsonify({"status": "ok", "service": "zimbra-backup-web"})


# =============================================================================
# Auth
# =============================================================================

@app.route("/login", methods=["GET", "POST"])
def login():
    if _current_user():
        return redirect(url_for("dashboard"))

    error = None
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        if not email or not password:
            error = t("login_fill_all", lang=g.lang)
        else:
            if USE_DEMO_AUTH:
                web_cfg = _load_web_config()
                local_users = web_cfg.get("local_users", {})
                success, user = authenticate_demo(email, password, local_users)
            else:
                web_cfg = _load_web_config()
                ldap_cfg = LDAPConfig.from_web_config(web_cfg)
                success, user = authenticate(email, password, ldap_cfg)

            if success and user:
                session.permanent = True
                session["user_email"] = user.email
                session["user_display_name"] = user.display_name
                session["user_is_admin"] = user.is_admin
                next_url = request.args.get("next", url_for("dashboard"))
                return redirect(next_url)
            else:
                error = t("login_invalid", lang=g.lang)
                logger.warning(f"Login fallido para: {email}")

    return render_template("login.html", error=error)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# =============================================================================
# Dashboard
# =============================================================================

@app.route("/")
@login_required
def dashboard():
    api = _get_api()
    search = request.args.get("search", "").strip()
    page = max(int(request.args.get("page", 1)), 1)
    per_page = 35
    sort_by = request.args.get("sort", "email")
    sort_order = request.args.get("order", "ASC")

    try:
        if g.user.is_admin:
            status_data = api.get_status()
            result = api.list_accounts(search=search or None, page=page, limit=per_page,
                                       sort_by=sort_by, sort_order=sort_order)
            accounts = result.get("accounts", [])
            total_accounts = result.get("total", 0)
            total_pages = max((total_accounts + per_page - 1) // per_page, 1)
        else:
            status_data = None
            total_accounts = 1
            total_pages = 1
            try:
                account = api.get_account(g.user.email)
                accounts = [account]
            except Exception:
                accounts = []
    except ConnectionError:
        flash("No se puede conectar al servicio de backup", "danger")
        status_data = None
        accounts = []
        total_accounts = 0
        total_pages = 1

    return render_template(
        "dashboard.html",
        user=g.user,
        status=status_data,
        accounts=accounts,
        search=search,
        page=page,
        total_pages=total_pages,
        total_accounts=total_accounts,
        sort_by=sort_by,
        sort_order=sort_order,
    )


# =============================================================================
# Emails
# =============================================================================

@app.route("/account/<path:email>/emails")
@login_required
def email_list(email):
    # Usuarios no-admin solo pueden ver su propia cuenta
    if not g.user.is_admin and email != g.user.email:
        abort(403)

    api = _get_api()
    folder = request.args.get("folder")
    search = request.args.get("search")
    date_from = request.args.get("date_from")
    date_to = request.args.get("date_to")
    page = max(int(request.args.get("page", 1)), 1)
    per_page = 35
    offset = (page - 1) * per_page
    sort_by = request.args.get("sort", "date")
    sort_order = request.args.get("order", "DESC")

    try:
        account = api.get_account(email)
        folders = account.get("folders", [])

        email_data = api.list_emails(
            email=email,
            folder=folder,
            search=search,
            date_from=date_from,
            date_to=date_to,
            limit=per_page,
            offset=offset,
            sort_by=sort_by,
            sort_order=sort_order,
        )
        emails = email_data.get("emails", [])
        total = email_data.get("total", 0)
        total_pages = (total + per_page - 1) // per_page

    except ConnectionError:
        flash("Error de conexión al servicio de backup", "danger")
        emails = []
        folders = []
        total = 0
        total_pages = 0
        account = {"email": email}

    return render_template(
        "emails.html",
        user=g.user,
        account=account,
        emails=emails,
        folders=folders,
        current_folder=folder,
        search=search,
        date_from=date_from,
        date_to=date_to,
        page=page,
        total_pages=total_pages,
        total=total,
        sort_by=sort_by,
        sort_order=sort_order,
    )


@app.route("/email/<int:email_id>")
@login_required
def email_detail(email_id):
    api = _get_api()
    try:
        email_record = api.get_email(email_id)

        # Verificar que el usuario tenga acceso a este email
        account_email = email_record.get("account_email", "")
        if not g.user.is_admin and account_email != g.user.email:
            abort(403)

        if email_record.get("deleted"):
            flash("Este email fue eliminado definitivamente", "warning")

        content = None
        if not email_record.get("deleted"):
            try:
                content = api.get_email_content(email_id)
            except Exception as e:
                flash(f"No se pudo cargar el contenido del email: {e}", "warning")

    except Exception as e:
        flash(f"Error: {e}", "danger")
        return redirect(url_for("dashboard"))

    return render_template(
        "email_detail.html",
        user=g.user,
        email=email_record,
        content=content,
        api_base=os.environ.get("BACKUP_API_URL", ""),
    )


@app.route("/email/<int:email_id>/download/<fmt>")
@login_required
def email_download(email_id, fmt):
    """Descarga un email individual en MD, PDF o DOCX."""
    if fmt not in ("md", "pdf", "docx"):
        flash("Formato inválido", "danger")
        return redirect(url_for("email_detail", email_id=email_id))

    api = _get_api()
    try:
        email_record = api.get_email(email_id)
        account_email = email_record.get("account_email", "")
        if not g.user.is_admin and account_email != g.user.email:
            abort(403)
        data = api.download_email(email_id, fmt)
    except Exception as e:
        flash(f"Error descargando email: {e}", "danger")
        return redirect(url_for("email_detail", email_id=email_id))

    mimes = {"md": "text/markdown", "pdf": "application/pdf",
              "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}
    exts = {"md": ".md", "pdf": ".pdf", "docx": ".docx"}
    subject_slug = "email"
    if email_record.get("subject"):
        import re
        s = re.sub(r"[^a-zA-Z0-9 _.-]", "", email_record["subject"])[:40].strip()
        if s:
            subject_slug = s
    return send_file(
        io.BytesIO(data),
        mimetype=mimes[fmt],
        as_attachment=True,
        download_name=f"{subject_slug}{exts[fmt]}",
    )


@app.route("/account/<path:email>/emails/download-zip", methods=["POST"])
@login_required
def emails_download_zip(email):
    """Descarga los emails seleccionados como ZIP en MD, PDF o DOCX."""
    if not g.user.is_admin and email != g.user.email:
        abort(403)

    email_ids = [int(i) for i in request.form.getlist("email_ids") if i.isdigit()]
    if not email_ids:
        flash("No se seleccionaron emails", "warning")
        return redirect(url_for("email_list", email=email))

    fmt = request.form.get("format", "md")
    if fmt not in ("md", "pdf", "docx"):
        fmt = "md"

    api = _get_api()
    try:
        zip_bytes = api.bulk_download_emails(email_ids, fmt)
    except Exception as e:
        flash(f"Error generando ZIP: {e}", "danger")
        return redirect(url_for("email_list", email=email))

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return send_file(
        io.BytesIO(zip_bytes),
        mimetype="application/zip",
        as_attachment=True,
        download_name=f"emails_backup_{ts}.zip",
    )


@app.route("/account/<path:email>/emails/send", methods=["POST"])
@login_required
def emails_send(email):
    """Envía los emails seleccionados por correo como ZIP adjunto."""
    if not g.user.is_admin and email != g.user.email:
        abort(403)

    email_ids = [int(i) for i in request.form.getlist("email_ids") if i.isdigit()]
    if not email_ids:
        flash("No se seleccionaron emails", "warning")
        return redirect(url_for("email_list", email=email))

    fmt = request.form.get("format", "md")
    if fmt not in ("md", "pdf", "docx"):
        fmt = "md"

    to_type = request.form.get("to_type", "mine")
    if to_type == "custom":
        to_addr = request.form.get("to_addr", "").strip()
        if not to_addr or "@" not in to_addr:
            flash("Dirección de email inválida", "warning")
            return redirect(url_for("email_list", email=email))
    else:
        to_addr = g.user.email

    web_cfg = _load_web_config()
    smtp_cfg = web_cfg.get("smtp", {})
    if not smtp_cfg.get("host"):
        flash("El servidor SMTP no está configurado en /config/web.json", "danger")
        return redirect(url_for("email_list", email=email))

    api = _get_api()
    try:
        zip_bytes = api.bulk_download_emails(email_ids, fmt)
        ext_label = fmt.upper()
        _send_zip_via_smtp(zip_bytes, to_addr, len(email_ids), smtp_cfg, ext_label)
        flash(f"{len(email_ids)} email(s) enviados a {to_addr}", "success")
    except Exception as e:
        flash(f"Error enviando emails: {e}", "danger")

    return redirect(url_for("email_list", email=email))


@app.route("/email/<int:email_id>/delete", methods=["POST"])
@admin_required
def email_delete(email_id):
    """Elimina un email definitivamente — solo administradores."""
    confirm = request.form.get("confirm") == "yes"
    if not confirm:
        flash("Debes confirmar la eliminación", "warning")
        return redirect(url_for("email_detail", email_id=email_id))

    api = _get_api()
    try:
        result = api.delete_email(email_id)
        flash(
            f"Email eliminado definitivamente de {result.get('deleted_from_snapshots', 0)} snapshots",
            "success",
        )
        return redirect(url_for("dashboard"))
    except Exception as e:
        flash(f"Error al eliminar: {e}", "danger")
        return redirect(url_for("email_detail", email_id=email_id))


# =============================================================================
# Admin panel
# =============================================================================

@app.route("/admin")
@admin_required
def admin_panel():
    api = _get_api()
    search = request.args.get("search", "").strip()
    page = max(int(request.args.get("page", 1)), 1)
    per_page = 35
    try:
        status_data = api.get_status()
        result = api.list_accounts(search=search or None, page=page, limit=per_page)
        accounts = result.get("accounts", [])
        total_accounts = result.get("total", 0)
        total_pages = max((total_accounts + per_page - 1) // per_page, 1)
        git_log = []
        try:
            git_log = api.get_git_log()
        except Exception:
            pass
    except ConnectionError:
        flash("No se puede conectar al servicio de backup", "danger")
        status_data = None
        accounts = []
        total_accounts = 0
        total_pages = 1
        git_log = []

    return render_template(
        "admin.html",
        user=g.user,
        status=status_data,
        accounts=accounts,
        total_accounts=total_accounts,
        search=search,
        page=page,
        total_pages=total_pages,
        git_log=git_log,
    )


@app.route("/admin/backup/run", methods=["POST"])
@admin_required
def admin_run_backup():
    api = _get_api()
    try:
        result = api.trigger_backup()
        flash(
            f"Backup ejecutado: {result.get('success', 0)} cuentas OK, "
            f"{result.get('emails_new', 0)} emails nuevos",
            "success",
        )
    except Exception as e:
        flash(f"Error: {e}", "danger")
    return redirect(url_for("admin_panel"))


@app.route("/admin/retention/apply", methods=["POST"])
@admin_required
def admin_apply_retention():
    api = _get_api()
    try:
        result = api.apply_retention()
        flash(
            f"Retención aplicada: {result.get('deleted', 0)} snapshots eliminados",
            "success",
        )
    except Exception as e:
        flash(f"Error: {e}", "danger")
    return redirect(url_for("admin_panel"))


@app.route("/admin/config/save", methods=["POST"])
@admin_required
def admin_config_save():
    api = _get_api()
    data = request.get_json(silent=True) or {}
    if not data:
        return jsonify({"ok": False, "error": "No se enviaron datos"}), 400
    try:
        result = api.update_config(data)
        return jsonify({"ok": True, "message": result.get("message", "Guardado")})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/admin/config/load")
@admin_required
def admin_config_load():
    api = _get_api()
    try:
        cfg = api.get_config()
        return jsonify({"ok": True, "data": cfg})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/admin/config/test-ssh", methods=["POST"])
@admin_required
def admin_config_test_ssh():
    api = _get_api()
    data = request.get_json(silent=True) or {}
    try:
        result = api.test_ssh(
            host=data.get("host", ""),
            user=data.get("user", ""),
            ssh_key=data.get("ssh_key", "/config/ssh/id_rsa"),
            ssh_port=int(data.get("ssh_port", 22)),
        )
        return jsonify({"ok": True, "data": result})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# =============================================================================
# Auth config (LDAP + local users)
# =============================================================================

@app.route("/admin/config/auth")
@admin_required
def admin_config_auth():
    cfg = _load_web_config()
    return jsonify({
        "ok": True,
        "data": {
            "ldap": cfg.get("ldap", {}),
            "local_users": cfg.get("local_users", {}),
        },
    })


@app.route("/admin/config/auth/save", methods=["POST"])
@admin_required
def admin_config_auth_save():
    import json as _json
    data = request.get_json(silent=True) or {}
    config_path = os.environ.get("WEB_CONFIG", "/config/web.json")

    try:
        with open(config_path) as f:
            cfg = _json.load(f)
    except Exception:
        cfg = {}

    if "ldap" in data:
        cfg["ldap"] = data["ldap"]
    if "local_users" in data:
        cfg["local_users"] = data["local_users"]

    with open(config_path, "w") as f:
        _json.dump(cfg, f, indent=2)

    return jsonify({"ok": True, "message": "Configuración de autenticación guardada"})


@app.route("/admin/config/test-ldap-bind", methods=["POST"])
@admin_required
def admin_test_ldap_bind():
    data = request.get_json(silent=True) or {}
    ldap_cfg = LDAPConfig(
        host=data.get("host", ""),
        port=int(data.get("port", 389)),
        bind_dn=data.get("bind_dn", ""),
        bind_password=data.get("bind_password", ""),
    )
    try:
        from ldap3 import Server, Connection, SIMPLE
        server = Server(ldap_cfg.host, port=ldap_cfg.port, connect_timeout=5)
        conn = Connection(server, user=ldap_cfg.bind_dn, password=ldap_cfg.bind_password,
                          authentication=SIMPLE, auto_bind=True)
        conn.unbind()
        return jsonify({"ok": True, "data": {"success": True, "message": "Conexión LDAP exitosa"}})
    except Exception as e:
        return jsonify({"ok": True, "data": {"success": False, "message": str(e)}})


@app.route("/admin/config/test-ldap-filter", methods=["POST"])
@admin_required
def admin_test_ldap_filter():
    data = request.get_json(silent=True) or {}
    ldap_cfg = LDAPConfig(
        host=data.get("host", ""),
        port=int(data.get("port", 389)),
        bind_dn=data.get("bind_dn", ""),
        bind_password=data.get("bind_password", ""),
        base_dn=data.get("base_dn", ""),
        user_filter=data.get("user_filter", "(mail={username})"),
    )
    test_email = data.get("test_email", "")
    if not test_email:
        return jsonify({"ok": True, "data": {"success": False, "message": "Ingrese un email de prueba"}})

    try:
        from ldap3 import Server, Connection, SIMPLE, SUBTREE
        server = Server(ldap_cfg.host, port=ldap_cfg.port, connect_timeout=5)
        conn = Connection(server, user=ldap_cfg.bind_dn, password=ldap_cfg.bind_password,
                          authentication=SIMPLE, auto_bind=True)
        search_filter = ldap_cfg.user_filter.format(username=test_email)
        conn.search(search_base=ldap_cfg.base_dn, search_filter=search_filter,
                    search_scope=SUBTREE, attributes=["mail", "cn"])
        found = len(conn.entries)
        names = []
        for e in conn.entries[:5]:
            names.append(str(getattr(e, "cn", e.entry_dn)))
        conn.unbind()
        if found:
            return jsonify({"ok": True, "data": {
                "success": True,
                "message": f"Encontrados {found} resultado(s): {', '.join(names)}",
                "count": found,
            }})
        else:
            return jsonify({"ok": True, "data": {
                "success": False,
                "message": f"Ningún resultado para '{test_email}' con filtro '{search_filter}'",
            }})
    except Exception as e:
        return jsonify({"ok": True, "data": {"success": False, "message": str(e)}})


# =============================================================================
# Reset
# =============================================================================

@app.route("/admin/reset/factory", methods=["POST"])
@admin_required
def admin_factory_reset():
    api = _get_api()
    try:
        result = api.factory_reset()
        flash(result.get("message", "Factory reset completado"), "success")
    except Exception as e:
        flash(f"Error en factory reset: {e}", "danger")
    return redirect(url_for("admin_panel"))


@app.route("/admin/reset/example", methods=["POST"])
@admin_required
def admin_example_reset():
    api = _get_api()
    try:
        result = api.example_reset()
        flash(result.get("message", "Contenido de ejemplo desplegado"), "success")
    except Exception as e:
        flash(f"Error desplegando ejemplo: {e}", "danger")
    return redirect(url_for("admin_panel"))


# =============================================================================
# Config export/import
# =============================================================================

@app.route("/admin/config/export", methods=["POST"])
@admin_required
def admin_config_export():
    api = _get_api()
    password = request.form.get("password", "").strip()
    try:
        data = api.export_config(password=password or None)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        if password:
            return send_file(io.BytesIO(data), mimetype="application/zip",
                             as_attachment=True,
                             download_name=f"zimbra_backup_config_{ts}.zip")
        return send_file(io.BytesIO(data), mimetype="application/json",
                         as_attachment=True,
                         download_name=f"zimbra_backup_config_{ts}.json")
    except Exception as e:
        flash(f"Error exportando: {e}", "danger")
        return redirect(url_for("admin_panel"))


@app.route("/admin/config/import", methods=["POST"])
@admin_required
def admin_config_import():
    if "file" not in request.files:
        flash("Se requiere un archivo", "danger")
        return redirect(url_for("admin_panel"))
    file = request.files["file"]
    if not file.filename:
        flash("Archivo vacío", "danger")
        return redirect(url_for("admin_panel"))
    password = request.form.get("password", "").strip()
    api = _get_api()
    try:
        result = api.import_config(
            file.read(), file.filename,
            password=password or None,
        )
        flash(result.get("message", "Configuración importada"), "success")
    except Exception as e:
        flash(f"Error importando: {e}", "danger")
    return redirect(url_for("admin_panel"))


# =============================================================================
# Logs
# =============================================================================

@app.route("/admin/logs")
@admin_required
def admin_logs():
    api = _get_api()
    lines = min(int(request.args.get("lines", 200)), 2000)
    try:
        log_data = api.get_logs(lines=lines)
    except Exception as e:
        log_data = {"content": f"Error: {e}", "lines": 0, "total_size": 0, "log_level": "?"}

    return render_template(
        "logs.html",
        user=g.user,
        log_content=log_data.get("content", ""),
        log_lines=log_data.get("lines", 0),
        log_size=log_data.get("total_size", 0),
        log_level=log_data.get("log_level", "INFO"),
        requested_lines=lines,
    )


@app.route("/admin/logs/level", methods=["POST"])
@admin_required
def admin_logs_level():
    level = request.form.get("level", "INFO").upper()
    if level not in ("DEBUG", "INFO", "WARNING", "ERROR"):
        level = "INFO"
    api = _get_api()
    try:
        api.update_config({"general": {"log_level": level}})
        flash(f"Nivel de log cambiado a {level}. El cambio se aplica en el próximo ciclo de backup.", "success")
    except Exception as e:
        flash(f"Error: {e}", "danger")
    return redirect(url_for("admin_logs"))


# =============================================================================
# Manual de Usuario
# =============================================================================

@app.route("/manual")
@login_required
def manual_view():
    import os as _os
    path = _os.path.join(_os.path.dirname(__file__), "manual.md")
    try:
        with open(path, encoding="utf-8") as f:
            content = f.read()
    except OSError:
        content = "# Error\n\nNo se pudo cargar el manual de usuario."
    return render_template("manual.html", user=g.user, content=content)


@app.route("/manual/download/md")
@login_required
def manual_download_md():
    import os as _os
    path = _os.path.join(_os.path.dirname(__file__), "manual.md")
    if not _os.path.exists(path):
        flash("Manual no disponible", "danger")
        return redirect(url_for("manual_view"))
    return send_file(path, mimetype="text/markdown", as_attachment=True,
                     download_name="Manual_de_Usuario_Zimbra_Backup.md")


@app.route("/manual/pdf")
@login_required
def manual_pdf_view():
    """Retorna el PDF inline para embeber en iframe."""
    import os as _os
    path = _os.path.join(_os.path.dirname(__file__), "manual.md")
    try:
        with open(path, encoding="utf-8") as f:
            content = f.read()
    except OSError:
        flash("Manual no disponible", "danger")
        return redirect(url_for("manual_view"))
    api = _get_api()
    try:
        pdf = api.md_to_pdf(content, title="Manual de Usuario - Zimbra Backup")
        return send_file(io.BytesIO(pdf), mimetype="application/pdf")
    except Exception as e:
        flash(f"Error generando PDF: {e}", "danger")
        return redirect(url_for("manual_view"))


@app.route("/manual/download/pdf")
@login_required
def manual_download_pdf():
    import os as _os
    path = _os.path.join(_os.path.dirname(__file__), "manual.md")
    try:
        with open(path, encoding="utf-8") as f:
            content = f.read()
    except OSError:
        flash("Manual no disponible", "danger")
        return redirect(url_for("manual_view"))
    api = _get_api()
    try:
        pdf = api.md_to_pdf(content, title="Manual de Usuario - Zimbra Backup")
        return send_file(io.BytesIO(pdf), mimetype="application/pdf",
                         as_attachment=True,
                         download_name="Manual_de_Usuario_Zimbra_Backup.pdf")
    except Exception as e:
        flash(f"Error generando PDF: {e}", "danger")
        return redirect(url_for("manual_view"))


# =============================================================================
# Error handlers
# =============================================================================

@app.errorhandler(403)
def forbidden(e):
    return render_template("error.html", code=403, message="Acceso denegado"), 403


@app.errorhandler(404)
def not_found(e):
    return render_template("error.html", code=404, message="Página no encontrada"), 404


@app.errorhandler(500)
def server_error(e):
    return render_template("error.html", code=500, message="Error interno del servidor"), 500


def create_app():
    return app


if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)
    app.run(host="0.0.0.0", port=8080, debug=True)
