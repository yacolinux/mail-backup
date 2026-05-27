#!/usr/bin/env python3
"""Genera emails de prueba en formato Maildir para desarrollo y testing.

Crea la estructura:
  dev-maildir/
    example.com/
      user1/Maildir/{cur,new,tmp,.Sent,.Trash}
      user2/Maildir/{cur,new,tmp}
"""

import os
import random
import string
import time
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from pathlib import Path

BASE_DIR = Path(os.environ.get("MAILDIR_OUTPUT", str(Path(__file__).parent.parent / "dev-maildir")))
UTC = timezone.utc


SUBJECTS = [
    "Reunión de equipo - semana próxima",
    "Informe mensual de ventas",
    "Actualización del proyecto",
    "Invitación: Workshop de capacitación",
    "Re: Consulta sobre facturación",
    "Recordatorio: Vencimiento de contrato",
    "Nueva política de trabajo remoto",
    "Resultados del Q3",
    "Bienvenido al equipo!",
    "Cambios en el sistema ERP",
    "Fwd: Importante - Leer antes del viernes",
    "Respuesta a tu consulta #2847",
    "Newsletter mensual - Diciembre 2024",
    "Solicitud de vacaciones aprobada",
    "Alerta de seguridad: cambio de contraseña requerido",
]

SENDERS = [
    ("Juan Pérez", "jperez@empresa.com"),
    ("María García", "mgarcia@empresa.com"),
    ("Carlos López", "clopez@empresa.com"),
    ("Sistema", "noreply@example.com"),
    ("RRHH", "rrhh@empresa.com"),
    ("Soporte Técnico", "soporte@empresa.com"),
    ("Cliente Externo", "cliente@otro-dominio.com"),
    ("Newsletter", "news@newsletters.com"),
]

BODIES = [
    "Estimado/a,\n\nLes informamos que se ha programado una reunión para el próximo lunes.\n\nSaludos cordiales.",
    "Adjunto encontrarás el informe solicitado. Por favor revisar antes del viernes.\n\nGracias.",
    "Te escribo para informarte sobre los cambios recientes en el sistema.\n\nQuedamos a disposición.",
    "Esta es una notificación automática del sistema. Por favor no responder.",
    "Buenos días,\n\nLe recordamos que su contrato vence el próximo mes.\n\nDepartamento de contratos.",
    "Hola,\n\nQuería consultarte sobre el estado del proyecto. ¿Podemos hablar esta tarde?\n\nSaludos.",
]

FOLDERS = ["INBOX", "Sent", "Trash", "Work", "Personal"]


def _unique_id():
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=20))


def _make_email(sender_name, sender_email, recipient_email, subject, body, date):
    msg = MIMEText(body, "plain", "utf-8")
    msg["Message-ID"] = f"<{_unique_id()}@example.com>"
    msg["Subject"] = subject
    msg["From"] = f"{sender_name} <{sender_email}>"
    msg["To"] = recipient_email
    msg["Date"] = date.strftime("%a, %d %b %Y %H:%M:%S +0000")
    return msg.as_bytes()


def _maildir_filename(date, flags="S"):
    ts = int(date.timestamp())
    unique = _unique_id()
    hostname = "backup.example.com"
    return f"{ts}.{unique}.{hostname}:2,{flags}"


def create_maildir_structure(base: Path, email: str, folders: list):
    """Crea la estructura Maildir para una cuenta."""
    username, domain = email.split("@")
    maildir = base / domain / username / "Maildir"

    # Crear subdirs estándar
    for d in ["cur", "new", "tmp"]:
        (maildir / d).mkdir(parents=True, exist_ok=True)

    # Crear subcarpetas IMAP
    for folder in folders[1:]:  # INBOX no necesita subcarpeta especial
        safe_folder = "." + folder
        for d in ["cur", "new", "tmp"]:
            (maildir / safe_folder / d).mkdir(parents=True, exist_ok=True)

    return maildir


def generate_emails_for_account(maildir: Path, recipient: str, count: int = 30):
    """Genera N emails aleatorios en el maildir."""
    now = datetime.now(UTC)

    for i in range(count):
        sender_name, sender_email = random.choice(SENDERS)
        subject = random.choice(SUBJECTS)
        body = random.choice(BODIES)
        folder = random.choice(FOLDERS)

        # Fecha aleatoria en los últimos 180 días
        days_ago = random.randint(0, 180)
        hours_ago = random.randint(0, 23)
        date = now - timedelta(days=days_ago, hours=hours_ago)

        # 80% de emails van a cur/ (leídos), 20% a new/ (sin leer)
        is_new = random.random() < 0.2
        flags = "" if is_new else "S"

        filename = _maildir_filename(date, flags)
        content = _make_email(sender_name, sender_email, recipient, subject, body, date)

        # Determinar carpeta
        if folder == "INBOX":
            subdir = maildir / ("new" if is_new else "cur")
        else:
            safe_folder = "." + folder
            subdir = maildir / safe_folder / ("new" if is_new else "cur")

        if not subdir.exists():
            subdir.mkdir(parents=True, exist_ok=True)

        filepath = subdir / filename
        filepath.write_bytes(content)

    print(f"  ✓ {count} emails generados para {recipient}")


def main():
    accounts = []

    # Admin account
    accounts.append(("admin@example.com", random.randint(70, 85)))

    # 69 additional user accounts (70 total)
    for i in range(1, 70):
        email = f"user{i:02d}@example.com"
        count = random.randint(70, 90)
        accounts.append((email, count))

    print(f"Generando estructura Maildir de prueba en {BASE_DIR}/")
    print(f"  {len(accounts)} cuentas, ~{sum(c for _, c in accounts)} emails en total")
    print("")

    for idx, (email, count) in enumerate(accounts, 1):
        maildir = create_maildir_structure(BASE_DIR, email, FOLDERS)
        generate_emails_for_account(maildir, email, count)
        if idx % 10 == 0:
            print(f"  [{idx}/{len(accounts)} cuentas creadas]")

    print("")
    total_emails = sum(c for _, c in accounts)
    print(f"✓ Estructura de prueba creada en {BASE_DIR}/")
    print(f"  {len(accounts)} cuentas, {total_emails} emails")
    print("")
    print("Para iniciar el backup con estos datos:")
    print("  make dev-up")
    print("  make dev-backup")


if __name__ == "__main__":
    main()
