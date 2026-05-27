"""Daemon de backup: scheduler APScheduler que ejecuta backups cada N horas."""

import logging
import logging.handlers
import os
import signal
import sys
import threading
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)
UTC = timezone.utc

_running = False
_scheduler = None


def setup_logging(log_path: str, log_level: str):
    """Configura el sistema de logging hacia archivo y stdout."""
    os.makedirs(os.path.dirname(log_path), exist_ok=True)

    level = getattr(logging, log_level.upper(), logging.INFO)
    fmt = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    datefmt = "%Y-%m-%d %H:%M:%S"

    handlers = [
        logging.StreamHandler(sys.stdout),
        logging.handlers.RotatingFileHandler(
            log_path, maxBytes=50 * 1024 * 1024, backupCount=5, encoding="utf-8"
        ),
    ]

    logging.basicConfig(level=level, format=fmt, datefmt=datefmt, handlers=handlers, force=True)

    # Reducir verbosidad de librerías externas
    logging.getLogger("apscheduler").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def run_backup_job(config, db):
    """Job ejecutado por el scheduler: realiza un ciclo completo de backup."""
    from .backup import BackupEngine
    logger.info("Iniciando backup programado...")
    try:
        engine = BackupEngine(config, db)
        result = engine.run(trigger="scheduled")
        logger.info(f"Backup programado completado: {result}")
    except Exception as e:
        logger.error(f"Error en backup programado: {e}", exc_info=True)


def start_daemon(config, db):
    """Inicia el daemon de backup con scheduler APScheduler.

    El scheduler ejecuta un backup cada `backup_interval_hours` horas.
    También ejecuta un backup inmediatamente al iniciar.
    """
    global _running, _scheduler

    # Import aquí para evitar importar APScheduler si no se necesita
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        from apscheduler.executors.pool import ThreadPoolExecutor
    except ImportError:
        logger.error("APScheduler no instalado. Ejecutar: pip install apscheduler")
        sys.exit(1)

    # Configurar logging completo
    import logging.handlers
    setup_logging(config.log_path, config.log_level)

    logger.info("=" * 60)
    logger.info("  Zimbra Backup System - Daemon iniciado")
    logger.info(f"  Intervalo: cada {config.backup_interval_hours} horas")
    logger.info(f"  Maildir base: {config.maildir_base}")
    logger.info(f"  Backup dir: {config.backup_dir}")
    logger.info(f"  Remoto: {'habilitado' if config.remote_enabled else 'deshabilitado'}")
    logger.info(f"  Git: {'habilitado' if config.git_enabled else 'deshabilitado'}")
    logger.info("=" * 60)

    _running = True
    stop_event = threading.Event()

    # Manejadores de señales para shutdown graceful
    def handle_signal(signum, frame):
        sig_name = signal.Signals(signum).name
        logger.info(f"Señal {sig_name} recibida, apagando daemon...")
        stop_event.set()

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    # Configurar APScheduler
    executors = {"default": ThreadPoolExecutor(1)}
    job_defaults = {
        "coalesce": True,       # Si el scheduler se retrasa, ejecutar solo 1 vez
        "max_instances": 1,     # No ejecutar backups en paralelo
        "misfire_grace_time": 600,  # 10 minutos de gracia si el job se pierde
    }

    _scheduler = BackgroundScheduler(
        executors=executors,
        job_defaults=job_defaults,
        timezone=config.timezone,
    )

    # Agregar job con intervalo
    _scheduler.add_job(
        run_backup_job,
        trigger="interval",
        hours=config.backup_interval_hours,
        id="zimbra_backup",
        name="Zimbra Mailbox Backup",
        kwargs={"config": config, "db": db},
        replace_existing=True,
    )

    _scheduler.start()
    logger.info(
        f"Scheduler iniciado. Próximo backup en {config.backup_interval_hours}h"
    )

    # Ejecutar backup inicial inmediatamente (en hilo separado para no bloquear)
    def initial_backup():
        logger.info("Ejecutando backup inicial al arranque...")
        run_backup_job(config, db)

    threading.Thread(target=initial_backup, daemon=True).start()

    # Esperar señal de parada
    stop_event.wait()

    logger.info("Deteniendo scheduler...")
    _scheduler.shutdown(wait=True)
    _running = False
    logger.info("Daemon detenido.")


def get_next_run_time() -> Optional[str]:
    """Retorna el próximo tiempo de ejecución del backup."""
    global _scheduler
    if _scheduler is None:
        return None
    job = _scheduler.get_job("zimbra_backup")
    if job and job.next_run_time:
        return job.next_run_time.isoformat()
    return None


def is_running() -> bool:
    return _running


def trigger_immediate_backup(config, db):
    """Dispara un backup inmediato fuera del schedule."""
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.get_job("zimbra_backup").modify(next_run_time=datetime.now(UTC))
        logger.info("Backup inmediato disparado via scheduler")
    else:
        # Fallback: ejecutar directamente
        run_backup_job(config, db)
