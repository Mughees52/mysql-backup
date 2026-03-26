import os
import shutil
from datetime import datetime, timedelta, timezone
from typing import List

from .logging_utils import get_logger


def make_backup_dir(base: str, instance: str, job_type: str) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    path = os.path.join(base, instance, job_type, ts)
    os.makedirs(path, exist_ok=True)
    return path


def list_backup_dirs(base: str, instance: str, job_type: str) -> List[str]:
    root = os.path.join(base, instance, job_type)
    if not os.path.isdir(root):
        return []
    entries = []
    for name in os.listdir(root):
        full = os.path.join(root, name)
        if os.path.isdir(full):
            entries.append(full)
    return sorted(entries)


def apply_retention_days(base: str, instance: str, job_type: str, days: int) -> None:
    logger = get_logger()
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    for path in list_backup_dirs(base, instance, job_type):
        name = os.path.basename(path)
        try:
            ts = datetime.strptime(name, "%Y%m%d-%H%M%S").replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if ts < cutoff:
            logger.info("Removing expired backup", extra={"path": path})
            shutil.rmtree(path, ignore_errors=True)


def apply_weekly_retention(base: str, instance: str, job_type: str, weeks: int) -> None:
    """
    Keep one backup per calendar week for the most recent `weeks` weeks, removing
    all but the latest backup within each older week outside the daily window.

    Daily backups are kept for the standard retention period, and one
    representative backup per week is preserved for the weekly window beyond that.
    """
    logger = get_logger()
    if weeks <= 0:
        return

    cutoff = datetime.now(timezone.utc) - timedelta(weeks=weeks)
    dirs = list_backup_dirs(base, instance, job_type)

    by_week: dict = {}
    for path in dirs:
        name = os.path.basename(path)
        try:
            ts = datetime.strptime(name, "%Y%m%d-%H%M%S").replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if ts < cutoff:
            logger.info("Removing backup outside weekly window", extra={"path": path})
            shutil.rmtree(path, ignore_errors=True)
            continue
        week_key = ts.isocalendar()[:2]  # (year, week_number)
        by_week.setdefault(week_key, []).append((ts, path))

    for week_key, entries in by_week.items():
        entries.sort(key=lambda x: x[0])
        for _, path in entries[:-1]:
            logger.info("Removing duplicate weekly backup", extra={"path": path, "week": week_key})
            shutil.rmtree(path, ignore_errors=True)

