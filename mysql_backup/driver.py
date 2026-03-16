import argparse
import os
import sys
from typing import List

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
    return parser.parse_args(argv)


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

    logger.info(
        "Starting backup run",
        extra={"job_names": [j.name for j in jobs], "dry_run": args.dry_run},
    )

    # Actual backup flows
    if args.dry_run:
        for job in jobs:
            logger.info("Dry run - would execute job", extra={"job": job.name, "type": job.type})
        return 0

    from .backup_logical import run_logical_backup
    from .backup_physical import run_physical_backup
    from .backup_binlog import run_binlog_backup

    exit_code = 0
    for job in jobs:
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

