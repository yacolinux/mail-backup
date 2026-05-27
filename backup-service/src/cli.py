"""CLI del sistema de backup Zimbra - comandos principales."""

import json
import logging
import os
import sys
from datetime import datetime
from typing import Optional

import click

from .config import load_config
from .db import Database

logger = logging.getLogger(__name__)


def _get_config_and_db():
    """Helper: carga config y abre la DB."""
    config = load_config()
    db = Database(config.db_path)
    return config, db


def _format_size(bytes_: int) -> str:
    """Formatea bytes en unidad legible."""
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
        return dt.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return iso_str[:16] if iso_str else "-"


@click.group()
@click.version_option("1.0.0", prog_name="zbackup")
def cli():
    """Zimbra Backup System - herramienta de gestión de backups de correo.

    \b
    Comandos principales:
      backup          Ejecutar backup ahora
      status          Estado del sistema
      list-accounts   Listar cuentas backupeadas
      list-emails     Listar emails de una cuenta
      list-backups    Listar snapshots de una cuenta
      delete-email    Eliminar definitivamente un email
      prune           Aplicar política de retención
      daemon          Iniciar daemon de backup
    """
    # Configurar logging básico para CLI
    logging.basicConfig(
        level=logging.WARNING,
        format="%(levelname)s: %(message)s",
    )


# =============================================================================
# backup
# =============================================================================

@cli.command()
@click.option(
    "--trigger",
    default="manual",
    type=click.Choice(["manual", "scheduled"]),
    help="Tipo de disparo del backup",
)
@click.option("--verbose", "-v", is_flag=True, help="Salida detallada")
def backup(trigger, verbose):
    """Ejecutar un backup completo ahora mismo."""
    if verbose:
        logging.getLogger().setLevel(logging.DEBUG)
    else:
        logging.getLogger().setLevel(logging.INFO)
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s: %(message)s",
        force=True,
    )

    config, db = _get_config_and_db()
    from .backup import BackupEngine
    engine = BackupEngine(config, db)

    click.echo(f"Iniciando backup ({trigger})...")
    try:
        result = engine.run(trigger=trigger)
        click.secho(
            f"\n✓ Backup completado: {result['success']} cuentas OK, "
            f"{result['failed']} errores, {result['emails_new']} emails nuevos",
            fg="green",
        )
    except Exception as e:
        click.secho(f"✗ Error: {e}", fg="red", err=True)
        sys.exit(1)


# =============================================================================
# status
# =============================================================================

@cli.command()
@click.option("--json", "as_json", is_flag=True, help="Salida en formato JSON")
def status(as_json):
    """Mostrar estado general del sistema de backup."""
    config, db = _get_config_and_db()
    stats = db.get_system_stats()
    runs = db.get_recent_runs(5)

    if as_json:
        click.echo(json.dumps({"stats": stats, "recent_runs": runs}, indent=2))
        return

    click.echo("")
    click.secho("=== Zimbra Backup System ===", bold=True)
    click.echo(f"  Cuentas backupeadas:  {stats['total_accounts']}")
    click.echo(f"  Emails indexados:     {stats['total_emails']:,}")
    click.echo(f"  Snapshots totales:    {stats['total_snapshots']}")
    click.echo(f"  Tamaño total:         {_format_size(stats['total_size_bytes'])}")

    if stats["last_run"]:
        lr = stats["last_run"]
        status_color = {"success": "green", "partial": "yellow", "failed": "red"}.get(
            lr.get("status", ""), "white"
        )
        click.echo(f"\n  Último backup:        {_format_date(lr.get('started_at'))}")
        click.echo(
            f"  Estado:               ",
            nl=False,
        )
        click.secho(lr.get("status", "?"), fg=status_color)
        click.echo(f"  Emails nuevos:        {lr.get('emails_new', 0)}")
    else:
        click.echo("\n  Sin backups realizados aún")

    click.echo("\n  Últimos 5 runs:")
    for run in runs:
        status_color = {"success": "green", "partial": "yellow", "failed": "red"}.get(
            run.get("status", ""), "white"
        )
        click.echo(
            f"    #{run['id']:4d}  {_format_date(run.get('started_at'))}  ",
            nl=False,
        )
        click.secho(f"{run.get('status', '?'):8}", fg=status_color, nl=False)
        click.echo(
            f"  {run.get('accounts_success', 0)}/{run.get('accounts_total', 0)} cuentas  "
            f"{run.get('emails_new', 0)} nuevos"
        )

    click.echo("")
    click.echo(f"  Config: {os.environ.get('BACKUP_CONFIG', '/config/backup.conf')}")
    if config.zimbra_remote_enabled:
        click.secho(
            f"  Modo:   remoto — extrayendo de {config.zimbra_remote.user}@{config.zimbra_remote.host}",
            fg="cyan",
        )
    else:
        click.echo(f"  Modo:   local — maildir en {config.maildir_base}")
    click.echo(
        f"  Backup secundario: {'habilitado (' + config.remote.host + ')' if config.remote_enabled else 'deshabilitado'}"
    )
    click.echo(f"  Git:    {'habilitado' if config.git_enabled else 'deshabilitado'}")
    click.echo(
        f"  Retención: {config.retention.hourly_keep}h / "
        f"{config.retention.daily_keep}d / "
        f"{config.retention.weekly_keep}w / "
        f"{config.retention.monthly_keep}m"
    )
    click.echo("")


# =============================================================================
# list-accounts
# =============================================================================

@cli.command("list-accounts")
@click.option("--format", "fmt", default="table", type=click.Choice(["table", "json", "plain"]))
def list_accounts(fmt):
    """Listar todas las cuentas de correo backupeadas."""
    _, db = _get_config_and_db()
    accounts = db.get_all_accounts()

    if not accounts:
        click.echo("No hay cuentas backupeadas aún.")
        return

    if fmt == "json":
        click.echo(json.dumps(accounts, indent=2))
        return

    if fmt == "plain":
        for a in accounts:
            click.echo(a["email"])
        return

    # Table format
    click.echo("")
    click.secho(
        f"{'EMAIL':<40} {'EMAILS':>8} {'ÚLTIMO BACKUP':<18} {'ESTADO'}",
        bold=True,
    )
    click.echo("-" * 80)
    for a in accounts:
        status_color = "green" if a.get("active") else "red"
        click.echo(
            f"{a['email']:<40} {a.get('total_emails', 0):>8,} "
            f"{_format_date(a.get('last_backup_at')):<18} ",
            nl=False,
        )
        click.secho("activa" if a.get("active") else "inactiva", fg=status_color)
    click.echo(f"\nTotal: {len(accounts)} cuentas")
    click.echo("")


# =============================================================================
# list-emails
# =============================================================================

@cli.command("list-emails")
@click.argument("account")
@click.option("--folder", "-f", default=None, help="Filtrar por carpeta IMAP")
@click.option("--search", "-s", default=None, help="Buscar en asunto o remitente")
@click.option("--from-date", default=None, help="Desde fecha (YYYY-MM-DD)")
@click.option("--to-date", default=None, help="Hasta fecha (YYYY-MM-DD)")
@click.option("--limit", "-l", default=50, show_default=True, help="Máximo de resultados")
@click.option("--offset", default=0, help="Saltear N resultados")
@click.option("--deleted", is_flag=True, help="Incluir emails eliminados")
@click.option("--format", "fmt", default="table", type=click.Choice(["table", "json", "plain"]))
def list_emails(account, folder, search, from_date, to_date, limit, offset, deleted, fmt):
    """Listar emails backupeados de una cuenta.

    ACCOUNT: Dirección de email (ej: usuario@dominio.com)
    """
    _, db = _get_config_and_db()
    acc = db.get_account_by_email(account)
    if not acc:
        click.secho(f"Cuenta no encontrada: {account}", fg="red", err=True)
        sys.exit(1)

    emails = db.get_account_emails(
        account_id=acc["id"],
        folder=folder,
        search=search,
        date_from=from_date,
        date_to=to_date,
        include_deleted=deleted,
        limit=limit,
        offset=offset,
    )

    total = db.count_account_emails_filtered(
        acc["id"], folder=folder, search=search, include_deleted=deleted
    )

    if not emails:
        click.echo(f"No se encontraron emails para {account}")
        return

    if fmt == "json":
        click.echo(json.dumps({"total": total, "emails": emails}, indent=2))
        return

    if fmt == "plain":
        for e in emails:
            click.echo(f"{e['id']}\t{e.get('date', '')}\t{e.get('from_addr', '')}\t{e.get('subject', '')}")
        return

    # Table
    click.echo(f"\nEmails de {account}  (mostrando {len(emails)}/{total})")
    click.secho(
        f"{'ID':>6}  {'FECHA':<17} {'DE':<30} {'ASUNTO':<40} {'CARPETA':<15} {'DEL'}",
        bold=True,
    )
    click.echo("-" * 115)
    for e in emails:
        is_deleted = bool(e.get("deleted"))
        from_s = (e.get("from_addr", "") or "")[:29]
        subject = (e.get("subject", "") or "(sin asunto)")[:39]
        folder_s = (e.get("folder", "INBOX") or "INBOX")[:14]
        date_s = _format_date(e.get("date", ""))[:16]

        line = (
            f"{e['id']:>6}  {date_s:<17} {from_s:<30} {subject:<40} {folder_s:<15} "
            f"{'✗' if is_deleted else ' '}"
        )
        click.secho(line, fg="red" if is_deleted else None)

    if total > limit + offset:
        remaining = total - limit - offset
        click.echo(f"\n... y {remaining} más. Usar --offset={limit + offset} para ver más")
    click.echo("")


# =============================================================================
# list-backups
# =============================================================================

@cli.command("list-backups")
@click.argument("account", required=False)
@click.option("--format", "fmt", default="table", type=click.Choice(["table", "json"]))
def list_backups(account, fmt):
    """Listar snapshots de backup de una cuenta (o todas si no se especifica).

    ACCOUNT: Dirección de email (opcional)
    """
    _, db = _get_config_and_db()

    if account:
        acc = db.get_account_by_email(account)
        if not acc:
            click.secho(f"Cuenta no encontrada: {account}", fg="red", err=True)
            sys.exit(1)
        snapshots = db.get_account_snapshots(acc["id"])
        data = {account: snapshots}
    else:
        accounts = db.get_all_accounts()
        data = {}
        for a in accounts:
            data[a["email"]] = db.get_account_snapshots(a["id"], limit=20)

    if fmt == "json":
        click.echo(json.dumps(data, indent=2))
        return

    for email_addr, snaps in data.items():
        click.echo(f"\n{email_addr}:")
        if not snaps:
            click.echo("  (sin snapshots)")
            continue
        click.secho(
            f"  {'ID':>6}  {'NOMBRE':<20} {'TIPO':<10} {'EMAILS':>8} {'TAMAÑO':>10} {'FECHA'}",
            bold=True,
        )
        click.echo("  " + "-" * 75)
        type_colors = {
            "hourly": "cyan", "daily": "blue", "weekly": "yellow", "monthly": "green"
        }
        for s in snaps:
            t = s.get("snapshot_type", "hourly")
            click.echo(f"  {s['id']:>6}  {s.get('snapshot_name',''):<20} ", nl=False)
            click.secho(f"{t:<10}", fg=type_colors.get(t, "white"), nl=False)
            click.echo(
                f" {s.get('email_count', 0):>8,} {_format_size(s.get('size_bytes', 0)):>10} "
                f"{_format_date(s.get('created_at', ''))}"
            )

    click.echo("")


# =============================================================================
# delete-email
# =============================================================================

@cli.command("delete-email")
@click.argument("email_id", type=int)
@click.option(
    "--confirm", is_flag=True,
    help="Confirmar eliminación permanente (REQUERIDO)"
)
@click.option("--json", "as_json", is_flag=True, help="Salida en JSON")
def delete_email(email_id, confirm, as_json):
    """Eliminar definitivamente un email backupeado (IRREVERSIBLE).

    EMAIL_ID: ID numérico del email (ver list-emails)

    Esta operación elimina el email de TODOS los snapshots y no puede deshacerse.
    """
    config, db = _get_config_and_db()

    # Verificar que existe
    email_record = db.get_email_by_id(email_id)
    if not email_record:
        click.secho(f"Error: Email ID {email_id} no encontrado", fg="red", err=True)
        sys.exit(1)

    if email_record.get("deleted"):
        click.secho(f"Email {email_id} ya fue eliminado anteriormente", fg="yellow")
        return

    if not confirm:
        click.echo("")
        click.secho("ATENCIÓN: Esta operación es IRREVERSIBLE", fg="red", bold=True)
        click.echo(f"  Email ID:  {email_id}")
        click.echo(f"  Asunto:    {email_record.get('subject', '?')}")
        click.echo(f"  De:        {email_record.get('from_addr', '?')}")
        click.echo(f"  Fecha:     {_format_date(email_record.get('date'))}")
        click.echo(f"  Archivo:   {email_record.get('filename', '?')}")
        click.echo("")
        click.echo("Para confirmar, agregar --confirm al comando")
        sys.exit(0)

    from .backup import BackupEngine
    engine = BackupEngine(config, db)

    try:
        result = engine.delete_email_permanently(email_id)
        if as_json:
            click.echo(json.dumps(result, indent=2))
        else:
            click.secho(
                f"✓ Email {email_id} eliminado de {result['deleted_from_snapshots']} snapshots",
                fg="green",
            )
    except Exception as e:
        click.secho(f"✗ Error: {e}", fg="red", err=True)
        sys.exit(1)


# =============================================================================
# prune
# =============================================================================

@cli.command()
@click.option("--account", default=None, help="Aplicar solo a una cuenta")
@click.option("--dry-run", is_flag=True, help="Simular sin eliminar nada")
def prune(account, dry_run):
    """Aplicar política de retención y eliminar snapshots expirados."""
    config, db = _get_config_and_db()
    from .retention import RetentionManager

    retention = RetentionManager(config, db)

    if dry_run:
        click.echo("Modo DRY-RUN: no se eliminará nada\n")
        if account:
            acc = db.get_account_by_email(account)
            if not acc:
                click.secho(f"Cuenta no encontrada: {account}", fg="red", err=True)
                sys.exit(1)
            summary = retention.get_retention_summary(acc["id"])
            accounts_summary = {account: summary}
        else:
            accounts_summary = {}
            for a in db.get_all_accounts():
                accounts_summary[a["email"]] = retention.get_retention_summary(a["id"])

        for email_addr, s in accounts_summary.items():
            click.echo(f"{email_addr}:")
            click.echo(f"  Snapshots totales:   {s['total_snapshots']}")
            click.echo(f"  A conservar:         {s['to_keep']}")
            click.secho(f"  A eliminar:          {s['to_delete']}", fg="yellow")
            for t, count in s["by_type"].items():
                click.echo(f"    - {t}: {count}")
        return

    if account:
        acc = db.get_account_by_email(account)
        if not acc:
            click.secho(f"Cuenta no encontrada: {account}", fg="red", err=True)
            sys.exit(1)
        result = retention.apply(acc["id"])
        click.secho(
            f"✓ {account}: {result['kept']} conservados, "
            f"{result['deleted']} eliminados, "
            f"{_format_size(result['freed_bytes'])} liberados",
            fg="green",
        )
    else:
        result = retention.apply_all()
        click.secho(
            f"✓ Retención aplicada: {result['deleted']} snapshots eliminados, "
            f"{_format_size(result['freed_bytes'])} liberados",
            fg="green",
        )


# =============================================================================
# daemon
# =============================================================================

@cli.command()
def daemon():
    """Iniciar el daemon de backup programado.

    El daemon ejecuta un backup cada N horas según la configuración.
    Responde a SIGTERM y SIGINT para un apagado graceful.
    """
    import logging.handlers
    config, db = _get_config_and_db()

    from .daemon import start_daemon
    start_daemon(config, db)


# =============================================================================
# init
# =============================================================================

@cli.command()
def init():
    """Inicializar el sistema de backup (crear directorios y BD)."""
    config, _ = _get_config_and_db()

    dirs = [
        config.backup_dir,
        os.path.dirname(config.db_path),
        os.path.dirname(config.log_path),
        config.git.repo_path if config.git_enabled else None,
        os.path.join(config.backup_dir, "accounts"),
    ]

    for d in dirs:
        if d:
            os.makedirs(d, exist_ok=True)
            click.echo(f"  ✓ {d}")

    if config.git_enabled:
        from .git_handler import GitHandler
        GitHandler(config)
        click.echo(f"  ✓ Repositorio git en {config.git.repo_path}")

    click.secho("✓ Sistema inicializado", fg="green")


# Entry point
def main():
    cli()


if __name__ == "__main__":
    main()
