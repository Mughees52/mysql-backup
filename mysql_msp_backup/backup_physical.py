import os
import shutil
from datetime import datetime, timezone
from typing import Optional

from .checks import check_disk_space, estimate_required_bytes
from .config import BackupConfig, JobConfig
from .dedup import link_dest_snapshot
from .encryption import build_xtrabackup_encryption_args, gpg_encrypt_directory
from .logging_utils import get_logger
from .mysql_client import (
    check_is_read_only,
    check_is_replica,
    estimate_database_size_bytes,
    kill_long_queries,
    set_pxc_desync,
)
from .shell_utils import run_with_retries
from .storage_local import apply_retention_days, apply_weekly_retention, list_backup_dirs, make_backup_dir
from .storage_remote import push_offsite

_FULL_BACKUP_CYCLE_DAYS = {"daily": 1, "weekly": 7}


def _previous_full_backup_dir(cfg: BackupConfig, instance_name: str) -> Optional[str]:
    dirs = list_backup_dirs(cfg.global_config.backup_root, instance_name, "physical")
    if not dirs:
        return None
    return dirs[-1]


def _should_force_full(opts: dict, base_dir: Optional[str]) -> bool:
    """
    Return True when a full backup is required regardless of backup_mode.

    Used when full_backup_cycle is set to weekly (or a day number 1-7) and
    no previous full exists, or the last full is older than the cycle.
    """
    cycle = opts.get("full_backup_cycle")
    if not cycle:
        return base_dir is None

    days = _FULL_BACKUP_CYCLE_DAYS.get(str(cycle).lower())
    if days is None:
        try:
            days = int(cycle)
        except (TypeError, ValueError):
            days = 7

    if base_dir is None:
        return True

    dir_name = os.path.basename(base_dir)
    try:
        ts = datetime.strptime(dir_name, "%Y%m%d-%H%M%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return True

    age_days = (datetime.now(timezone.utc) - ts).days
    return age_days >= days


def run_physical_backup(cfg: BackupConfig, job: JobConfig) -> None:
    logger = get_logger()
    instance = cfg.instances[job.instance]
    global_cfg = cfg.global_config

    # Replica / read-only safety gates
    if instance.replica_only and not check_is_replica(instance):
        logger.warning(
            "Skipping physical backup: instance is not a running replica",
            extra={"job": job.name, "instance": instance.name},
        )
        return
    if instance.read_only_only and not check_is_read_only(instance):
        logger.warning(
            "Skipping physical backup: instance is not read-only",
            extra={"job": job.name, "instance": instance.name},
        )
        return

    approx_db_bytes = estimate_database_size_bytes(instance)
    required = estimate_required_bytes(approx_db_bytes, factor=global_cfg.disk_space_factor)

    if not check_disk_space(global_cfg.backup_root, required):
        raise RuntimeError("Insufficient disk space for physical backup")

    opts = job.backup_options
    backup_tool = opts.get("tool", "xtrabackup")
    tool_path = opts.get("xtrabackup_path") or opts.get("mariadb_backup_path") or backup_tool

    # Determine incremental vs full
    requested_incremental = opts.get("backup_mode") == "incremental"
    previous_full = _previous_full_backup_dir(cfg, instance.name)
    force_full = _should_force_full(opts, previous_full)
    incremental = requested_incremental and not force_full
    base_dir = previous_full if incremental else None

    if requested_incremental and force_full:
        logger.info(
            "Forcing full backup due to full_backup_cycle policy",
            extra={"job": job.name},
        )

    # Kill long-running queries before backup if requested
    if opts.get("kill_long_queries", False):
        kill_long_queries(
            instance,
            threshold_seconds=int(opts.get("kill_queries_timeout", 10)),
            query_type=str(opts.get("kill_query_type", "select")),
        )

    backup_dir = make_backup_dir(global_cfg.backup_root, instance.name, "physical")
    logger.info("Starting physical backup", extra={"job": job.name, "backup_dir": backup_dir, "incremental": incremental})

    defaults_file = opts.get("defaults_file")
    cmd = [tool_path]
    if defaults_file:
        cmd.append(f"--defaults-file={defaults_file}")
    cmd.extend([
        "--backup",
        f"--host={instance.host}",
        f"--port={instance.port}",
        f"--user={instance.user}",
        f"--target-dir={backup_dir}",
    ])
    if not defaults_file and instance.password:
        cmd.append(f"--password={instance.password}")
    if instance.socket:
        cmd.append(f"--socket={instance.socket}")

    if incremental and base_dir:
        cmd.extend(["--incremental-basedir", base_dir])

    # Save replica position in backup (for point-in-time recovery)
    if opts.get("save_replica_info", False):
        cmd.append("--slave-info")

    # Compression algorithm (e.g. zstd for xtrabackup 8.0.34+)
    compress_algo = opts.get("compression_algorithm")
    if compress_algo:
        cmd.extend(["--compress", f"--compress-algorithm={compress_algo}"])

    # Built-in AES-256 encryption
    cmd.extend(build_xtrabackup_encryption_args(opts))

    extra_args = opts.get("extra_args") or []
    if isinstance(extra_args, list):
        cmd.extend(extra_args)

    timeout = float(global_cfg.default_timeout_seconds)

    if instance.pxc and instance.pxc_desync:
        set_pxc_desync(instance, True)

    try:
        run_with_retries(cmd, check=True, timeout=timeout)

        if opts.get("prepare_after_backup", True):
            # Encrypted backups must be decrypted before prepare: xtrabackup
            # cannot apply the redo log to .xbcrypt files directly.
            if opts.get("use_xtra_encryption"):
                algo = opts.get("xtra_encrypt_algo", "AES256")
                decrypt_cmd = [tool_path]
                if defaults_file:
                    decrypt_cmd.append(f"--defaults-file={defaults_file}")
                decrypt_cmd.extend([f"--decrypt={algo}", f"--target-dir={backup_dir}"])
                key_file = opts.get("xtra_key_file")
                key = opts.get("xtra_key") or (
                    __import__("os").getenv(opts.get("xtra_key_env", "XTRABACKUP_ENCRYPTION_KEY"))
                    if not key_file else None
                )
                if key_file:
                    decrypt_cmd.append(f"--encrypt-key-file={key_file}")
                elif key:
                    decrypt_cmd.append(f"--encrypt-key={key}")
                run_with_retries(decrypt_cmd, check=True, timeout=timeout)

            prepare_cmd = [tool_path]
            if defaults_file:
                prepare_cmd.append(f"--defaults-file={defaults_file}")
            prepare_cmd.extend([f"--target-dir={backup_dir}", "--prepare"])
            prepare_memory = opts.get("prepare_memory")
            if prepare_memory:
                prepare_cmd.append(f"--use-memory={prepare_memory}")
            run_with_retries(prepare_cmd, check=True, timeout=timeout)

        # Optional post-backup verification (re-prepare in read-only mode as sanity check)
        if opts.get("verify_after_backup", False):
            verify_cmd = [tool_path, f"--target-dir={backup_dir}", "--prepare", "--export"]
            run_with_retries(verify_cmd, check=True, timeout=timeout)
            logger.info("Backup verification passed", extra={"job": job.name})

    finally:
        if instance.pxc and instance.pxc_desync:
            set_pxc_desync(instance, False)

    # GPG encryption after completion if configured
    recipient = opts.get("gpg_recipient")
    if recipient:
        output_path = backup_dir.rstrip("/") + ".tar.gz.gpg"
        gpg_encrypt_directory(backup_dir, output_path, recipient)

    # Dedup snapshot
    if job.dedup:
        previous = _previous_full_backup_dir(cfg, instance.name)
        if previous:
            link_dest_snapshot(previous, backup_dir)

    if job.offsite_targets:
        push_offsite(cfg, job.name, backup_dir, job.offsite_targets)

    retention = job.retention_days if job.retention_days is not None else int(global_cfg.default_retention_days)
    apply_retention_days(global_cfg.backup_root, instance.name, "physical", retention)

    weekly_weeks = job.weekly_retention_weeks if job.weekly_retention_weeks is not None else int(global_cfg.weekly_retention_weeks)
    if weekly_weeks > 0:
        apply_weekly_retention(global_cfg.backup_root, instance.name, "physical", weekly_weeks)

    # Enforce backup_copies limit: keep only the N most recent physical backups
    backup_copies = int(opts.get("backup_copies", 0))
    if backup_copies > 0:
        all_dirs = list_backup_dirs(global_cfg.backup_root, instance.name, "physical")
        for stale in all_dirs[:-backup_copies]:
            logger.info("Removing old backup copy", extra={"path": stale})
            shutil.rmtree(stale, ignore_errors=True)

    logger.info("Physical backup completed", extra={"job": job.name, "backup_dir": backup_dir})

