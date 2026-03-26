import os
import shutil
from pathlib import Path
from typing import Tuple

from .checks import check_disk_space
from .config import BackupConfig, JobConfig
from .logging_utils import get_logger
from .mysql_client import get_master_status
from .shell_utils import run_with_retries
from .storage_local import apply_retention_days, make_backup_dir
from .storage_remote import push_offsite

_BINLOG_MIN_FREE_BYTES = 512 * 1024 * 1024  # 512 MB minimum free space default


def _state_file_path(cfg: BackupConfig, job: JobConfig) -> Path:
    base = Path(cfg.global_config.tmp_dir)
    base.mkdir(parents=True, exist_ok=True)
    return base / f"binlog_state_{job.name}.txt"


def _load_state(path: Path) -> Tuple[str, int]:
    if not path.is_file():
        return "", 4
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return "", 4
    file_name, pos_s = text.split(":", 1)
    return file_name, int(pos_s)


def _save_state(path: Path, file_name: str, pos: int) -> None:
    path.write_text(f"{file_name}:{pos}\n", encoding="utf-8")


def _check_binlog_disk_free(backup_root: str, min_free_pct: float) -> bool:
    """Return False if free disk space is below min_free_pct percent of total."""
    os.makedirs(backup_root, exist_ok=True)
    usage = shutil.disk_usage(backup_root)
    free_pct = (usage.free / usage.total) * 100 if usage.total > 0 else 100.0
    return free_pct >= min_free_pct


def run_binlog_backup(cfg: BackupConfig, job: JobConfig) -> None:
    logger = get_logger()
    instance = cfg.instances[job.instance]
    global_cfg = cfg.global_config

    opts = job.backup_options

    # Disk free percentage guard
    min_free_pct = float(opts.get("min_free_disk_pct", 5.0))
    if not _check_binlog_disk_free(global_cfg.backup_root, min_free_pct):
        raise RuntimeError(
            f"Binlog backup aborted: free disk space is below {min_free_pct}% on {global_cfg.backup_root}"
        )

    backup_dir = make_backup_dir(global_cfg.backup_root, instance.name, "binlog")
    logger.info("Starting binlog backup", extra={"job": job.name, "backup_dir": backup_dir})

    mysqlbinlog_path = opts.get("mysqlbinlog_path", "/usr/bin/mysqlbinlog")
    gpg_recipient = opts.get("gpg_recipient")

    state_path = _state_file_path(cfg, job)
    last_file, last_pos = _load_state(state_path)

    out_path = os.path.join(backup_dir, "binlog.sql")

    cmd = [
        mysqlbinlog_path,
        "--read-from-remote-server",
        f"--host={instance.host}",
        f"--port={instance.port}",
        f"--user={instance.user}",
    ]
    if instance.password:
        cmd.append(f"--password={instance.password}")

    # Allow unencrypted/insecure connections (useful for internal networks)
    if opts.get("insecure_connection", False):
        cmd.append("--ssl-mode=DISABLED")

    # Determine starting file/position
    if last_file:
        start_file, start_pos = last_file, last_pos
    else:
        binlog_file = opts.get("binlog_file")
        if not binlog_file:
            binlog_prefix = opts.get("binlog_log_prefix", "mysql-bin")
            raise RuntimeError(
                f"binlog_file must be set in backup_options for the first binlog run "
                f"(expected format: '{binlog_prefix}.000001')"
            )
        start_file, start_pos = binlog_file, 4

    cmd.append(f"--start-position={start_pos}")
    cmd.append(start_file)
    cmd.extend(["--result-file", out_path])

    timeout = float(global_cfg.default_timeout_seconds)
    run_with_retries(cmd, check=True, timeout=timeout)

    new_file, new_pos = get_master_status(instance)
    _save_state(state_path, new_file, new_pos)

    if job.offsite_targets:
        push_offsite(cfg, job.name, backup_dir, job.offsite_targets)

    # Per-job binlog retention takes priority over global setting
    binlog_retention = opts.get("binlog_retention_days")
    if binlog_retention is not None:
        retention = int(binlog_retention)
    elif job.retention_days is not None:
        retention = job.retention_days
    else:
        retention = int(global_cfg.default_retention_days)

    apply_retention_days(global_cfg.backup_root, instance.name, "binlog", retention)

    logger.info("Binlog backup completed", extra={"job": job.name, "backup_dir": backup_dir})

