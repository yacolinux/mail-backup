"""Operaciones sobre Maildir: escaneo, parsing de headers, indexado."""

import email
import email.header
import email.policy
import logging
import os
from pathlib import Path
from typing import Dict, Generator, List, Optional

logger = logging.getLogger(__name__)

# Subcarpetas estándar de Maildir
MAILDIR_SUBDIRS = {"cur", "new", "tmp"}


def _decode_header(value: str) -> str:
    """Decodifica un header de email (puede estar en base64/quoted-printable)."""
    if not value:
        return ""
    try:
        parts = email.header.decode_header(value)
        decoded = []
        for part, charset in parts:
            if isinstance(part, bytes):
                try:
                    decoded.append(part.decode(charset or "utf-8", errors="replace"))
                except (LookupError, UnicodeDecodeError):
                    decoded.append(part.decode("latin-1", errors="replace"))
            else:
                decoded.append(str(part))
        return " ".join(decoded).strip()
    except Exception:
        return str(value)


def _parse_maildir_flags(filename: str) -> str:
    """Extrae los flags de Maildir del nombre del archivo.

    Formato Maildir: <unique>:2,<flags>
    Flags comunes: D=draft, F=flagged, P=passed, R=replied, S=seen, T=trashed
    """
    if ":2," in filename:
        return filename.split(":2,", 1)[1]
    return ""


def _folder_from_path(path: str, maildir_root: str) -> str:
    """Determina el nombre de la carpeta IMAP a partir de la ruta."""
    try:
        rel = Path(path).relative_to(maildir_root)
        parts = rel.parts
        # La estructura es: [.FolderName/]cur|new/filename
        # O directamente: cur|new/filename (INBOX)
        if len(parts) >= 2 and parts[0].startswith("."):
            # Nombre de carpeta IMAP (sin el punto inicial, con / como separador)
            folder = parts[0][1:].replace(".", "/")
            return folder or "INBOX"
    except ValueError:
        pass
    return "INBOX"


def parse_email_headers(filepath: str, maildir_root: str = None) -> Dict:
    """Parsea solo los headers de un archivo de email Maildir.

    Lee únicamente los headers (no el body) para eficiencia.
    Retorna dict con la metadata del email.
    """
    result = {
        "filename": os.path.basename(filepath),
        "message_id": "",
        "subject": "",
        "from_addr": "",
        "to_addr": "",
        "date": "",
        "size_bytes": 0,
        "folder": "INBOX",
        "maildir_flags": _parse_maildir_flags(os.path.basename(filepath)),
    }

    try:
        stat = os.stat(filepath)
        result["size_bytes"] = stat.st_size
    except OSError:
        pass

    if maildir_root:
        result["folder"] = _folder_from_path(filepath, maildir_root)

    try:
        # Leer solo los headers (hasta línea vacía)
        with open(filepath, "rb") as f:
            # Leer hasta 8KB para headers (suficiente para casi todos los casos)
            raw_headers = b""
            for line in f:
                if line in (b"\n", b"\r\n"):
                    break
                raw_headers += line
                if len(raw_headers) > 8192:
                    break

        msg = email.message_from_bytes(
            raw_headers + b"\n\n", policy=email.policy.compat32
        )

        result["message_id"] = _decode_header(msg.get("Message-ID", "")).strip("<>")
        result["subject"] = _decode_header(msg.get("Subject", "(sin asunto)"))
        result["from_addr"] = _decode_header(msg.get("From", ""))
        result["to_addr"] = _decode_header(msg.get("To", ""))

        date_str = msg.get("Date", "")
        if date_str:
            try:
                from email.utils import parsedate_to_datetime
                dt = parsedate_to_datetime(date_str)
                result["date"] = dt.isoformat()
            except Exception:
                result["date"] = date_str

    except Exception as e:
        logger.debug(f"Error parseando headers de {filepath}: {e}")

    return result


def scan_maildir(root_path: str) -> Generator[Dict, None, None]:
    """Escanea un directorio Maildir y genera dicts con metadata de cada email.

    Soporta:
    - Maildir plano: cur/, new/ en el root
    - Maildir con subcarpetas IMAP: .FolderName/cur/, .FolderName/new/
    """
    root = Path(root_path)
    if not root.exists():
        logger.warning(f"Maildir no existe: {root_path}")
        return

    # Encuentra todos los directorios cur/ y new/ (recursivo)
    for dirpath, dirnames, filenames in os.walk(root_path):
        dir_name = os.path.basename(dirpath)
        if dir_name in ("cur", "new"):
            # Excluir tmp/
            parent = os.path.basename(os.path.dirname(dirpath))
            # Verificar que es un directorio Maildir válido
            # (el padre debe ser Maildir root o una subcarpeta .FolderName)
            for filename in filenames:
                if filename.startswith("."):
                    continue  # Ignorar archivos ocultos
                filepath = os.path.join(dirpath, filename)
                if os.path.isfile(filepath):
                    try:
                        meta = parse_email_headers(filepath, root_path)
                        yield meta
                    except Exception as e:
                        logger.error(f"Error procesando {filepath}: {e}")


def get_maildir_stats(root_path: str) -> Dict:
    """Calcula estadísticas del maildir: total de emails y tamaño."""
    total_count = 0
    total_size = 0
    folders = set()

    for meta in scan_maildir(root_path):
        total_count += 1
        total_size += meta.get("size_bytes", 0)
        folders.add(meta.get("folder", "INBOX"))

    return {
        "email_count": total_count,
        "total_size_bytes": total_size,
        "folders": sorted(folders),
    }


def get_email_content(filepath: str) -> Optional[Dict]:
    """Lee el contenido completo de un email para visualización en web.

    Retorna el cuerpo en text/plain y text/html (si existen),
    más lista de attachments.
    """
    if not os.path.exists(filepath):
        return None

    try:
        with open(filepath, "rb") as f:
            msg = email.message_from_bytes(f.read(), policy=email.policy.compat32)

        result = {
            "headers": {
                "subject": _decode_header(msg.get("Subject", "(sin asunto)")),
                "from": _decode_header(msg.get("From", "")),
                "to": _decode_header(msg.get("To", "")),
                "cc": _decode_header(msg.get("Cc", "")),
                "date": msg.get("Date", ""),
                "message_id": msg.get("Message-ID", ""),
            },
            "text_plain": None,
            "text_html": None,
            "attachments": [],
        }

        if msg.is_multipart():
            for part in msg.walk():
                content_type = part.get_content_type()
                disposition = part.get_content_disposition()

                if disposition == "attachment":
                    result["attachments"].append({
                        "filename": _decode_header(
                            part.get_filename("attachment")
                        ),
                        "content_type": content_type,
                        "size": len(part.get_payload(decode=True) or b""),
                    })
                elif content_type == "text/plain" and result["text_plain"] is None:
                    payload = part.get_payload(decode=True)
                    charset = part.get_content_charset("utf-8")
                    result["text_plain"] = payload.decode(
                        charset, errors="replace"
                    ) if payload else ""
                elif content_type == "text/html" and result["text_html"] is None:
                    payload = part.get_payload(decode=True)
                    charset = part.get_content_charset("utf-8")
                    result["text_html"] = payload.decode(
                        charset, errors="replace"
                    ) if payload else ""
        else:
            content_type = msg.get_content_type()
            payload = msg.get_payload(decode=True)
            charset = msg.get_content_charset("utf-8")
            if payload:
                text = payload.decode(charset, errors="replace")
                if content_type == "text/html":
                    result["text_html"] = text
                else:
                    result["text_plain"] = text

        return result

    except Exception as e:
        logger.error(f"Error leyendo email {filepath}: {e}")
        return None


def find_email_file(snapshot_path: str, filename: str) -> Optional[str]:
    """Busca un archivo de email en un snapshot de Maildir."""
    for dirpath, dirnames, filenames in os.walk(snapshot_path):
        if filename in filenames:
            return os.path.join(dirpath, filename)
    return None


def list_folders(maildir_root: str) -> List[str]:
    """Lista todas las carpetas IMAP disponibles en un maildir."""
    root = Path(maildir_root)
    folders = ["INBOX"]
    if root.exists():
        for item in root.iterdir():
            if item.is_dir() and item.name.startswith("."):
                folder = item.name[1:].replace(".", "/")
                if folder:
                    folders.append(folder)
    return sorted(set(folders))
