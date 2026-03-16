import os
from typing import Optional

from .checks import check_disk_space, estimate_required_bytes
from .config import BackupConfig, JobConfig
from .logging_utils import get_logger
from .mysql_client import estimate_database_size_bytes
from .shell_utils import run
from .storage_local import apply_retention_days, make_backup_dir
from .storage_remote import push_offsite


def run_logical_backup(cfg: BackupConfig, job: JobConfig) -> None:
    logger = get_logger()
    instance = cfg.instances[job.instance]
    global_cfg = cfg.global_config

    approx_db_bytes = estimate_database_size_bytes(instance)
    required = estimate_required_bytes(approx_db_bytes)

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

    extra_args = opts.get("extra_args") or []
    if isinstance(extra_args, list):
        cmd.extend(extra_args)

    run(cmd, check=True)

    if job.offsite_targets:
        push_offsite(cfg, job.name, backup_dir, job.offsite_targets)

    apply_retention_days(
        global_cfg.backup_root,
        instance.name,
        "logical",
        int(global_cfg.default_retention_days),
    )

    logger.info("Logical backup completed", extra={"job": job.name, "backup_dir": backup_dir})

