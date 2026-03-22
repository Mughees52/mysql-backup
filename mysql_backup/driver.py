import argparse
import fcntl
import os
import sys
import time
from datetime import datetime, timezone
from typing import List, Optional

from .config import BackupConfig, ConfigError, JobConfig, load_config, validate_config
from .logging_utils import setup_logging, get_logger


def _default_config_path() -> str:
    home = os.path.expanduser("~")
    return os.path.join(home, ".config", "mysql-backup", "config.yml")


def _parse_args(argv: List[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="MySQL/MariaDB backup driver")
    parser.add_argument("--config", help="Path to config YAML file", default=_default_config_path())
    parser.add_argument("--job", help="Run a specific job by name")
    parser.add_argument("--type", choices=["logical", "physical", "binlog"], help="Filter jobs by type")
    parser.add_argument("--list-jobs", action="store_true", help="List configured jobs and exit")
    parser.add_argument("--dry-run", action="store_true", help="Show what would run without executing")
    parser.add_argument("--validate-config", action="store_true", help="Validate configuration and exit")
    parser.add_argument("--self-test", action="store_true", help="Run a lightweight self-test and exit")
    parser.add_argument(
        "--run-scheduled",
        action="store_true",
        help=(
            "Run only the jobs whose schedule_hint is due at the current minute. "
            "Designed to be called from a single '* * * * *' cron entry — the "
            "driver reads the schedule from config.yml and decides what to run."
        ),
    )
    parser.add_argument(
        "--lock-file",
        help=(
            "Path to a lock file used to prevent concurrent driver instances. "
            "Useful when running multiple configs on the same host. "
            "Defaults to /tmp/mysql-backup-driver.lock"
        ),
        default=None,
    )
    return parser.parse_args(argv)


class _LockFile:
    """Non-blocking exclusive lock file to prevent duplicate driver instances."""

    def __init__(self, path: str) -> None:
        self._path = path
        self._fh = None

    def acquire(self) -> bool:
        self._fh = open(self._path, "w")
        try:
            fcntl.flock(self._fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._fh.write(str(os.getpid()))
            self._fh.flush()
            return True
        except OSError:
            self._fh.close()
            self._fh = None
            return False

    def release(self) -> None:
        if self._fh:
            fcntl.flock(self._fh, fcntl.LOCK_UN)
            self._fh.close()
            self._fh = None
            try:
                os.unlink(self._path)
            except OSError:
                pass


def _is_job_due(schedule_hint: str, now: datetime) -> bool:
    """
    Return True if the job's cron schedule_hint was due within the last 60 seconds.

    Uses croniter to compute the most recent scheduled time before `now`. If that
    time falls within the last minute, the job is considered due for this invocation.
    Invocations from a '* * * * *' cron entry therefore fire each job at exactly
    the right minute as defined by its schedule_hint.
    """
    try:
        from croniter import croniter, CroniterBadCronError
    except ImportError:
        return False
    try:
        cron = croniter(schedule_hint, now)
        prev = cron.get_prev(datetime)
        return (now - prev).total_seconds() < 60
    except (CroniterBadCronError, Exception):
        return False


def _check_graceful_stop(stop_file: Optional[str]) -> bool:
    """Return True if the graceful-stop sentinel file exists."""
    if stop_file and os.path.exists(stop_file):
        return True
    return False


def _load_and_setup(args: argparse.Namespace) -> BackupConfig:
    cfg = load_config(args.config)
    setup_logging(cfg.global_config.log_dir)
    logger = get_logger()
    logger.info("Loaded configuration", extra={"config_path": args.config})
    return cfg


def _select_jobs(cfg: BackupConfig, args: argparse.Namespace) -> List[JobConfig]:
    jobs = list(cfg.jobs.values())
    if args.job:
        jobs = [j for j in jobs if j.name == args.job]
    if args.type:
        jobs = [j for j in jobs if j.type == args.type]
    if args.run_scheduled:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        jobs = [
            j for j in jobs
            if j.schedule_hint and _is_job_due(j.schedule_hint, now)
        ]
    return jobs


def main(argv: List[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    args = _parse_args(argv)

    try:
        cfg = _load_and_setup(args)
    except (IOError, OSError, ConfigError) as exc:
        print(f"Failed to load config: {exc}", file=sys.stderr)
        return 1

    from .logging_utils import get_logger

    logger = get_logger()

    # Config validation only
    if args.validate_config:
        try:
            validate_config(cfg)
        except ConfigError as exc:
            print(f"Config validation failed: {exc}", file=sys.stderr)
            return 1
        print("Config validation OK")
        return 0

    # Lightweight self-test: validate config and list jobs
    if args.self_test:
        try:
            validate_config(cfg)
        except ConfigError as exc:
            print(f"Self-test: config validation failed: {exc}", file=sys.stderr)
            return 1
        jobs = list(cfg.jobs.values())
        print(f"Self-test OK. Found {len(jobs)} jobs.")
        return 0

    jobs = _select_jobs(cfg, args)
    if args.list_jobs:
        for job in jobs:
            print(f"{job.name} [{job.type}] on instance {job.instance}")
        return 0

    if not jobs:
        logger.warning("No jobs selected")
        return 0

    # Acquire exclusive lock file (prevents duplicate concurrent runs for this config)
    lock_path = args.lock_file or "/tmp/mysql-backup-driver.lock"
    lock = _LockFile(lock_path)
    if not lock.acquire():
        logger.warning("Another backup driver instance is already running", extra={"lock_file": lock_path})
        return 0

    try:
        return _run_jobs(cfg, jobs, args, logger)
    finally:
        lock.release()


def _run_jobs(cfg: BackupConfig, jobs: List[JobConfig], args: argparse.Namespace, logger) -> int:
    from .backup_logical import run_logical_backup
    from .backup_physical import run_physical_backup
    from .backup_binlog import run_binlog_backup

    graceful_stop_file = cfg.global_config.graceful_stop_file

    logger.info(
        "Starting backup run",
        extra={"job_names": [j.name for j in jobs], "dry_run": args.dry_run},
    )

    if args.dry_run:
        for job in jobs:
            logger.info("Dry run - would execute job", extra={"job": job.name, "type": job.type})
        return 0

    exit_code = 0
    for job in jobs:
        # Check graceful-stop file before each job (allows clean shutdown between jobs)
        if _check_graceful_stop(graceful_stop_file):
            logger.info(
                "Graceful stop requested - halting before next job",
                extra={"stop_file": graceful_stop_file, "pending_job": job.name},
            )
            break

        try:
            if job.type == "logical":
                run_logical_backup(cfg, job)
            elif job.type == "physical":
                run_physical_backup(cfg, job)
            elif job.type == "binlog":
                run_binlog_backup(cfg, job)
            else:
                logger.info("Job type not yet implemented", extra={"job": job.name, "type": job.type})
        except Exception:
            logger.exception("Job failed", extra={"job": job.name, "type": job.type})
            exit_code = 1

    return exit_code


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

