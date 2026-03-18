import os

from .checks import check_disk_space, estimate_required_bytes
from .config import BackupConfig, JobConfig
from .logging_utils import get_logger
from .mysql_client import check_is_read_only, check_is_replica, estimate_database_size_bytes
from .shell_utils import run
from .storage_local import apply_retention_days, apply_weekly_retention, make_backup_dir
from .storage_remote import push_offsite


def run_logical_backup(cfg: BackupConfig, job: JobConfig) -> None:
    logger = get_logger()
    instance = cfg.instances[job.instance]
    global_cfg = cfg.global_config

    # Replica / read-only safety gates
    if instance.replica_only and not check_is_replica(instance):
        logger.warning(
            "Skipping logical backup: instance is not a running replica",
            extra={"job": job.name, "instance": instance.name},
        )
        return
    if instance.read_only_only and not check_is_read_only(instance):
        logger.warning(
            "Skipping logical backup: instance is not read-only",
            extra={"job": job.name, "instance": instance.name},
        )
        return

    approx_db_bytes = estimate_database_size_bytes(instance)
    required = estimate_required_bytes(approx_db_bytes, factor=global_cfg.disk_space_factor)

    if not check_disk_space(global_cfg.backup_root, required):
        raise RuntimeError("Insufficient disk space for logical backup")

    backup_dir = make_backup_dir(global_cfg.backup_root, instance.name, "logical")
    logger.info("Starting logical backup", extra={"job": job.name, "backup_dir": backup_dir})

    opts = job.backup_options
    mydumper_path = opts.get("mydumper_path", "/usr/bin/mydumper")
    threads = int(opts.get("threads", 4))
    chunk_filesize = int(opts.get("chunk_filesize", 64))
    rows = int(opts.get("rows", 50000))
    compress = bool(opts.get("compress", True))

    cmd = [
        mydumper_path,
        f"--host={instance.host}",
        f"--port={instance.port}",
        f"--user={instance.user}",
        f"--outputdir={backup_dir}",
        f"--threads={threads}",
        f"--chunk-filesize={chunk_filesize}",
        f"--rows={rows}",
    ]
    if instance.password:
        cmd.append(f"--password={instance.password}")
    if compress:
        cmd.append("--compress")

    # Dump stored triggers alongside tables
    if opts.get("dump_triggers", False):
        cmd.append("--triggers")

    # Use less-locking mode (reduces metadata lock hold time)
    if opts.get("less_locking", False):
        cmd.append("--less-locking")

    # NUMA-aware allocation (requires numactl on the host)
    if opts.get("use_numa", False):
        cmd.append("--use-numa")

    # FTWRL guardian: abort the backup if a FTWRL lock takes too long
    if opts.get("ftwrl_guardian", False):
        cmd.append("--use-ftwrl-guardian")

    # Incremental logical: only dump tables modified in the last N days
    incremental_since = opts.get("incremental_since_days")
    if incremental_since is not None:
        cmd.append(f"--updated-since={int(incremental_since)}")

    extra_args = opts.get("extra_args") or []
    if isinstance(extra_args, list):
        cmd.extend(extra_args)

    run(cmd, check=True)

    if job.offsite_targets:
        push_offsite(cfg, job.name, backup_dir, job.offsite_targets)

    retention = job.retention_days if job.retention_days is not None else int(global_cfg.default_retention_days)
    apply_retention_days(global_cfg.backup_root, instance.name, "logical", retention)

    weekly_weeks = job.weekly_retention_weeks if job.weekly_retention_weeks is not None else int(global_cfg.weekly_retention_weeks)
    if weekly_weeks > 0:
        apply_weekly_retention(global_cfg.backup_root, instance.name, "logical", weekly_weeks)

    logger.info("Logical backup completed", extra={"job": job.name, "backup_dir": backup_dir})

