"""Política de retención GFS (Grandfather-Father-Son) para snapshots.

La política funciona así:
  - Período horario:   mantiene UN snapshot por intervalo de 2hs durante N horas
  - Período diario:    mantiene UN snapshot por día durante N días
  - Período semanal:   mantiene UN snapshot por semana durante N semanas
  - Período mensual:   mantiene UN snapshot por mes durante N meses
  - Más antiguo que todo: se elimina

Esto permite retención de hasta varios años con un número acotado de snapshots.
"""

import logging
import os
import shutil
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Set, Tuple

logger = logging.getLogger(__name__)
UTC = timezone.utc


def _parse_ts(ts_str: str) -> datetime:
    """Parsea un timestamp ISO 8601 a datetime con timezone UTC."""
    if not ts_str:
        return datetime.min.replace(tzinfo=UTC)
    try:
        # Intentar con Z suffix
        if ts_str.endswith("Z"):
            ts_str = ts_str[:-1] + "+00:00"
        dt = datetime.fromisoformat(ts_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt
    except ValueError:
        return datetime.min.replace(tzinfo=UTC)


def classify_snapshots(
    snapshots: List[Dict],
    hourly_keep: int,
    daily_keep: int,
    weekly_keep: int,
    monthly_keep: int,
) -> Tuple[Set[int], List[Dict]]:
    """Determina qué snapshots conservar y cuáles eliminar.

    Returns:
        (keep_ids: Set[int], to_delete: List[Dict])
    """
    if not snapshots:
        return set(), []

    now = datetime.now(UTC)

    # Ordenar por fecha de creación (más reciente primero)
    sorted_snaps = sorted(
        snapshots,
        key=lambda s: _parse_ts(s.get("created_at", "")),
        reverse=True,
    )

    keep_ids: Set[int] = set()

    # === Período HORARIO ===
    # Conservar los últimos `hourly_keep` snapshots
    hourly_kept = 0
    for snap in sorted_snaps:
        if hourly_kept >= hourly_keep:
            break
        keep_ids.add(snap["id"])
        hourly_kept += 1

    # Calcular cuándo termina el período horario
    if hourly_kept > 0:
        # El snapshot más antiguo del período horario
        hourly_snapshots = [s for s in sorted_snaps if s["id"] in keep_ids]
        if hourly_snapshots:
            hourly_boundary = _parse_ts(hourly_snapshots[-1].get("created_at", ""))
        else:
            hourly_boundary = now - timedelta(hours=hourly_keep * 2)
    else:
        hourly_boundary = now

    # === Período DIARIO ===
    # De los snapshots NO en período horario, conservar el último de cada día
    daily_by_day: Dict[str, Dict] = {}
    for snap in sorted_snaps:
        if snap["id"] in keep_ids:
            continue
        ts = _parse_ts(snap.get("created_at", ""))
        day_key = ts.strftime("%Y-%m-%d")
        if day_key not in daily_by_day:
            daily_by_day[day_key] = snap

    # Conservar sólo los N días más recientes
    daily_days = sorted(daily_by_day.keys(), reverse=True)[:daily_keep]
    for day in daily_days:
        keep_ids.add(daily_by_day[day]["id"])

    # Calcular frontera diaria
    if daily_days:
        oldest_daily_day = daily_days[-1]
        daily_boundary = datetime.strptime(oldest_daily_day, "%Y-%m-%d").replace(
            tzinfo=UTC
        )
    else:
        daily_boundary = hourly_boundary

    # === Período SEMANAL ===
    # De los NO conservados aún, conservar el último de cada semana ISO
    weekly_by_week: Dict[str, Dict] = {}
    for snap in sorted_snaps:
        if snap["id"] in keep_ids:
            continue
        ts = _parse_ts(snap.get("created_at", ""))
        week_key = ts.strftime("%G-W%V")  # ISO week
        if week_key not in weekly_by_week:
            weekly_by_week[week_key] = snap

    weekly_weeks = sorted(weekly_by_week.keys(), reverse=True)[:weekly_keep]
    for week in weekly_weeks:
        keep_ids.add(weekly_by_week[week]["id"])

    # === Período MENSUAL ===
    # De los NO conservados aún, conservar el último de cada mes
    monthly_by_month: Dict[str, Dict] = {}
    for snap in sorted_snaps:
        if snap["id"] in keep_ids:
            continue
        ts = _parse_ts(snap.get("created_at", ""))
        month_key = ts.strftime("%Y-%m")
        if month_key not in monthly_by_month:
            monthly_by_month[month_key] = snap

    monthly_months = sorted(monthly_by_month.keys(), reverse=True)[:monthly_keep]
    for month in monthly_months:
        keep_ids.add(monthly_by_month[month]["id"])

    # === Snapshots a eliminar ===
    to_delete = [s for s in snapshots if s["id"] not in keep_ids]

    logger.debug(
        f"Retención: {len(keep_ids)} conservados, {len(to_delete)} a eliminar. "
        f"Horarios={hourly_kept}, Diarios={len(daily_days)}, "
        f"Semanales={len(weekly_weeks)}, Mensuales={len(monthly_months)}"
    )

    return keep_ids, to_delete


def _reclassify_snapshot(snap: Dict, now: datetime, ret_cfg) -> str:
    """Determina el tipo correcto de un snapshot según su antigüedad."""
    ts = _parse_ts(snap.get("created_at", ""))
    age = now - ts

    hourly_period = timedelta(hours=ret_cfg.hourly_keep * 2)
    daily_period = timedelta(days=ret_cfg.daily_keep)
    weekly_period = timedelta(weeks=ret_cfg.weekly_keep)

    if age <= hourly_period:
        return "hourly"
    elif age <= daily_period:
        return "daily"
    elif age <= weekly_period:
        return "weekly"
    else:
        return "monthly"


class RetentionManager:
    """Aplica la política de retención a los snapshots de cada cuenta."""

    def __init__(self, config, db):
        self.config = config
        self.db = db
        self.ret = config.retention

    def apply(self, account_id: int) -> Dict:
        """Aplica la política de retención para una cuenta.

        Returns:
            Dict con estadísticas de la operación.
        """
        snapshots = self.db.get_account_snapshots(account_id, limit=10000)
        if not snapshots:
            return {"kept": 0, "deleted": 0, "freed_bytes": 0}

        keep_ids, to_delete = classify_snapshots(
            snapshots,
            hourly_keep=self.ret.hourly_keep,
            daily_keep=self.ret.daily_keep,
            weekly_keep=self.ret.weekly_keep,
            monthly_keep=self.ret.monthly_keep,
        )

        # Reclasificar los que se conservan según su antigüedad
        now = datetime.now(UTC)
        for snap in snapshots:
            if snap["id"] in keep_ids:
                correct_type = _reclassify_snapshot(snap, now, self.ret)
                if correct_type != snap.get("snapshot_type"):
                    self.db.update_snapshot_type(snap["id"], correct_type)

        # Eliminar snapshots expirados
        freed_bytes = 0
        deleted_count = 0
        for snap in to_delete:
            try:
                freed = self._delete_snapshot(snap)
                freed_bytes += freed
                deleted_count += 1
                self.db.delete_snapshot(snap["id"])
                logger.info(
                    f"Snapshot eliminado: {snap.get('snapshot_name')} "
                    f"({snap.get('snapshot_type')}, {freed / 1024 / 1024:.1f} MB liberados)"
                )
            except Exception as e:
                logger.error(f"Error eliminando snapshot {snap.get('id')}: {e}")

        return {
            "kept": len(keep_ids),
            "deleted": deleted_count,
            "freed_bytes": freed_bytes,
        }

    def apply_all(self) -> Dict:
        """Aplica retención a todas las cuentas activas."""
        accounts = self.db.get_all_accounts()
        total = {"kept": 0, "deleted": 0, "freed_bytes": 0}
        for account in accounts:
            result = self.apply(account["id"])
            for k in total:
                total[k] += result[k]
        logger.info(
            f"Retención aplicada: {total['deleted']} snapshots eliminados, "
            f"{total['freed_bytes'] / 1024 / 1024:.1f} MB liberados"
        )
        return total

    def _delete_snapshot(self, snapshot: Dict) -> int:
        """Elimina el directorio de un snapshot y retorna bytes liberados.

        NOTA: Gracias a los hardlinks, solo se libera espacio cuando
        el último enlace a un archivo es eliminado. Los emails compartidos
        con otros snapshots NO liberan espacio hasta que TODOS los snapshots
        que los referencian sean eliminados.
        """
        snap_path = snapshot.get("snapshot_path", "")
        freed = 0

        if not snap_path or not os.path.exists(snap_path):
            logger.debug(f"Snapshot path no existe: {snap_path}")
            return 0

        try:
            # Calcular espacio real liberado (solo inodos únicos)
            freed = self._calc_unique_size(snap_path)
            shutil.rmtree(snap_path, ignore_errors=True)
            logger.debug(f"Directorio eliminado: {snap_path}")
        except Exception as e:
            logger.error(f"Error eliminando {snap_path}: {e}")

        return freed

    def _calc_unique_size(self, path: str) -> int:
        """Calcula el tamaño de archivos con nlink == 1 (sin hardlinks externos).

        Solo los archivos con un único enlace liberarán espacio real al eliminar.
        """
        total = 0
        try:
            for dirpath, _, filenames in os.walk(path):
                for filename in filenames:
                    filepath = os.path.join(dirpath, filename)
                    try:
                        stat = os.lstat(filepath)
                        if stat.st_nlink == 1:
                            total += stat.st_size
                    except OSError:
                        pass
        except Exception:
            pass
        return total

    def get_retention_summary(self, account_id: int) -> Dict:
        """Retorna un resumen de cómo quedaría la retención sin aplicarla."""
        snapshots = self.db.get_account_snapshots(account_id, limit=10000)
        keep_ids, to_delete = classify_snapshots(
            snapshots,
            hourly_keep=self.ret.hourly_keep,
            daily_keep=self.ret.daily_keep,
            weekly_keep=self.ret.weekly_keep,
            monthly_keep=self.ret.monthly_keep,
        )

        by_type: Dict[str, int] = defaultdict(int)
        for snap in snapshots:
            if snap["id"] in keep_ids:
                now = datetime.now(UTC)
                t = _reclassify_snapshot(snap, now, self.ret)
                by_type[t] += 1

        return {
            "total_snapshots": len(snapshots),
            "to_keep": len(keep_ids),
            "to_delete": len(to_delete),
            "by_type": dict(by_type),
            "config": {
                "hourly_keep": self.ret.hourly_keep,
                "daily_keep": self.ret.daily_keep,
                "weekly_keep": self.ret.weekly_keep,
                "monthly_keep": self.ret.monthly_keep,
            },
        }
