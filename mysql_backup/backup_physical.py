import os
from typing import Optional

from .checks import check_disk_space, estimate_required_bytes
from .config import BackupConfig, JobConfig
from .dedup import link_dest_snapshot
from .encryption import build_xtrabackup_encryption_args, gpg_encrypt_directory
from .logging_utils import get_logger
from .mysql_client import estimate_database_size_bytes, set_pxc_desync
from .shell_utils import run_with_retries
from .storage_local import apply_retention_days, list_backup_dirs, make_backup_dir
from .storage_remote import push_offsite


def _previous_full_backup_dir(cfg: BackupConfig, instance_name: str) -> Optional[str]:
    dirs = list_backup_dirs(cfg.global_config.backup_root, instance_name, "physical")
    if not dirs:
        return None
    return dirs[-1]


def run_physical_backup(cfg: BackupConfig, job: JobConfig) -> None:
    logger = get_logger()
    instance = cfg.instances[job.instance]
    global_cfg = cfg.global_config

    approx_db_bytes = estimate_database_size_bytes(instance)
    required = estimate_required_bytes(approx_db_bytes)

    if not check_disk_space(global_cfg.backup_root, required):
        raise RuntimeError("Insufficient disk space for physical backup")

    backup_dir = make_backup_dir(global_cfg.backup_root, instance.name, "physical")
    logger.info("Starting physical backup", extra={"job": job.name, "backup_dir": backup_dir})

    opts = job.backup_options
    backup_tool = opts.get("tool", "xtrabackup")  # or mariadb-backup
    tool_path = opts.get("xtrabackup_path") or opts.get("mariadb_backup_path") or backup_tool

    incremental = opts.get("backup_mode") == "incremental"
    base_dir = _previous_full_backup_dir(cfg, instance.name) if incremental else None

    cmd = [tool_path, f"--host={instance.host}", f"--port={instance.port}", f"--user={instance.user}", f"--target-dir={backup_dir}"]
    if instance.password:
        cmd.append(f"--password={instance.password}")
    if instance.socket:
        cmd.append(f"--socket={instance.socket}")

    if incremental and base_dir:
        cmd.extend(["--incremental-basedir", base_dir])

    # Built-in encryption
    cmd.extend(build_xtrabackup_encryption_args(opts))

    extra_args = opts.get("extra_args") or []
    if isinstance(extra_args, list):
        cmd.extend(extra_args)

    timeout = float(global_cfg.default_timeout_seconds)

    # If PXC with desync enabled, desync before backup
    if instance.pxc and instance.pxc_desync:
        set_pxc_desync(instance, True)

    try:
        run_with_retries(cmd, check=True, timeout=timeout)

        if opts.get("prepare_after_backup", True):
            run_with_retries([tool_path, f"--target-dir={backup_dir}", "--prepare"], check=True, timeout=timeout)
    finally:
        if instance.pxc and instance.pxc_desync:
            set_pxc_desync(instance, False)

    # GPG encryption after completion if configured
    recipient = opts.get("gpg_recipient")
    if recipient:
        output_path = backup_dir.rstrip("/") + ".tar.gz.gpg"
        gpg_encrypt_directory(backup_dir, output_path, recipient)

    # Dedup snapshot if enabled
    if job.dedup:
        previous = _previous_full_backup_dir(cfg, instance.name)
        if previous:
            link_dest_snapshot(previous, backup_dir)

    if job.offsite_targets:
        push_offsite(cfg, job.name, backup_dir, job.offsite_targets)

    apply_retention_days(
        global_cfg.backup_root,
        instance.name,
        "physical",
        int(global_cfg.default_retention_days),
    )

    logger.info("Physical backup completed", extra={"job": job.name, "backup_dir": backup_dir})

