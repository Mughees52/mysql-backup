import argparse
import os
import shutil
from typing import List

from .config import BackupConfig, ConfigError, JobConfig, load_config, validate_config
from .logging_utils import get_logger, setup_logging
from .mysql_client import estimate_database_size_bytes, get_connection


def _default_config_path() -> str:
    home = os.path.expanduser("~")
    return os.path.join(home, ".config", "mysql-msp-backup", "config.yml")


def _parse_args(argv: List[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Pre-checks for mysql-msp-backup")
    parser.add_argument("--config", help="Path to config YAML file", default=_default_config_path())
    parser.add_argument("--job", help="Limit checks to a specific job name")
    parser.add_argument("--instance", help="Limit checks to a specific instance name")
    return parser.parse_args(argv)


def _check_path_exists(path: str, description: str) -> List[str]:
    if not shutil.which(path) and not os.path.exists(path):
        return [f"{description} not found or not executable: {path}"]
    return []


def _check_instance_connectivity(cfg: BackupConfig, instance_name: str) -> List[str]:
    from .config import InstanceConfig

    errors: List[str] = []
    inst = cfg.instances.get(instance_name)
    if not inst:
        return [f"Instance {instance_name} not defined in config"]
    logger = get_logger()
    try:
        with get_connection(inst) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
    except Exception as exc:
        logger.exception("MySQL connectivity check failed", extra={"instance": instance_name})
        errors.append(f"MySQL connectivity failed for instance {instance_name}: {exc}")
        return errors

    # Try to estimate DB size (also exercises information_schema access)
    try:
        _ = estimate_database_size_bytes(inst)
    except Exception as exc:
        logger.exception("Database size estimation failed", extra={"instance": instance_name})
        errors.append(f"Failed to estimate database size for instance {instance_name}: {exc}")

    return errors


def _precheck_job(cfg: BackupConfig, job: JobConfig) -> List[str]:
    errors: List[str] = []
    inst = cfg.instances[job.instance]

    # Check instance connectivity
    errors.extend(_check_instance_connectivity(cfg, inst.name))

    opts = job.backup_options

    # Tool-specific checks
    if job.type == "logical":
        mydumper_path = opts.get("mydumper_path", "/usr/bin/mydumper")
        errors.extend(_check_path_exists(mydumper_path, "mydumper binary"))
    elif job.type == "physical":
        tool = opts.get("tool", "xtrabackup")
        tool_path = opts.get("xtrabackup_path") or opts.get("mariadb_backup_path") or tool
        errors.extend(_check_path_exists(tool_path, "physical backup tool (xtrabackup/mariadb-backup)"))
        if opts.get("use_xtra_encryption"):
            key_env = opts.get("xtra_key_env", "XTRABACKUP_ENCRYPTION_KEY")
            if not os.getenv(key_env):
                errors.append(f"Env var {key_env} is required for xtrabackup encryption but is not set")
    elif job.type == "binlog":
        mysqlbinlog_path = opts.get("mysqlbinlog_path", "/usr/bin/mysqlbinlog")
        errors.extend(_check_path_exists(mysqlbinlog_path, "mysqlbinlog binary"))

    # Basic directory checks
    for desc, path in [
        ("backup_root", cfg.global_config.backup_root),
        ("log_dir", cfg.global_config.log_dir),
        ("tmp_dir", cfg.global_config.tmp_dir),
    ]:
        base = os.path.dirname(path) or "."
        if not os.path.exists(base):
            errors.append(f"Base directory for {desc} does not exist: {base}")

    return errors


def main(argv: List[str] | None = None) -> int:
    import sys

    if argv is None:
        argv = sys.argv[1:]
    args = _parse_args(argv)

    try:
        cfg = load_config(args.config)
    except (IOError, OSError, ConfigError) as exc:
        print(f"Failed to load config: {exc}", file=sys.stderr)
        return 1

    setup_logging(cfg.global_config.log_dir, name="mysql_msp_backup_precheck")
    logger = get_logger("mysql_msp_backup_precheck")

    # Structural config validation first
    try:
        validate_config(cfg)
    except ConfigError as exc:
        print(f"Precheck: config validation failed: {exc}", file=sys.stderr)
        return 1

    # Select jobs/instances to check
    jobs = list(cfg.jobs.values())
    if args.job:
        jobs = [j for j in jobs if j.name == args.job]
    if args.instance:
        jobs = [j for j in jobs if j.instance == args.instance]

    if not jobs:
        print("No jobs selected for precheck", file=sys.stderr)
        return 1

    all_errors: List[str] = []
    for job in jobs:
        logger.info("Running precheck for job", extra={"job": job.name, "type": job.type})
        errs = _precheck_job(cfg, job)
        if errs:
            prefix = f"[job={job.name}] "
            all_errors.extend(prefix + e for e in errs)

    if all_errors:
        print("Precheck FAILED. Issues found:", file=sys.stderr)
        for e in all_errors:
            print(f" - {e}", file=sys.stderr)
        return 1

    print("Precheck OK for selected jobs")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

