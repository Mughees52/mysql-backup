import os
from pathlib import Path
from typing import Tuple

from .config import BackupConfig, JobConfig
from .logging_utils import get_logger
from .mysql_client import get_master_status
from .shell_utils import run_with_retries
from .storage_local import apply_retention_days, make_backup_dir
from .storage_remote import push_offsite


def _state_file_path(cfg: BackupConfig, job: JobConfig) -> Path:
    base = Path(cfg.global_config.tmp_dir)
    base.mkdir(parents=True, exist_ok=True)
    return base / f"binlog_state_{job.name}.txt"


def _load_state(path: Path) -> Tuple[str, int]:
    if not path.is_file():
        return "", 4  # start near beginning
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return "", 4
    file_name, pos_s = text.split(":", 1)
    return file_name, int(pos_s)


def _save_state(path: Path, file_name: str, pos: int) -> None:
    path.write_text(f"{file_name}:{pos}\n", encoding="utf-8")


def run_binlog_backup(cfg: BackupConfig, job: JobConfig) -> None:
    logger = get_logger()
    instance = cfg.instances[job.instance]
    global_cfg = cfg.global_config

    backup_dir = make_backup_dir(global_cfg.backup_root, instance.name, "binlog")
    logger.info("Starting binlog backup", extra={"job": job.name, "backup_dir": backup_dir})

    opts = job.backup_options
    mysqlbinlog_path = opts.get("mysqlbinlog_path", "/usr/bin/mysqlbinlog")
    gpg_recipient = opts.get("gpg_recipient")

    state_path = _state_file_path(cfg, job)
    last_file, last_pos = _load_state(state_path)

    # Simple approach: dump from last known position (or beginning) to now
    out_path = os.path.join(backup_dir, "binlog.sql")

    cmd = [
        mysqlbinlog_path,
        f"--host={instance.host}",
        f"--port={instance.port}",
        f"--user={instance.user}",
    ]
    if instance.password:
        cmd.append(f"--password={instance.password}")

    # Determine starting file/position
    if last_file:
        start_file, start_pos = last_file, last_pos
    else:
        # First run: use configured binlog_file and start from position 4
        binlog_file = opts.get("binlog_file")
        if not binlog_file:
            raise RuntimeError("binlog_file must be set in backup_options for first binlog run")
        start_file, start_pos = binlog_file, 4

    cmd.append(f"--start-position={start_pos}")
    cmd.append(start_file)

    cmd.extend(["--result-file", out_path])

    timeout = float(global_cfg.default_timeout_seconds)
    run_with_retries(cmd, check=True, timeout=timeout)

    # Record new master status after backup for next run
    new_file, new_pos = get_master_status(instance)
    _save_state(state_path, new_file, new_pos)

    if job.offsite_targets:
        push_offsite(cfg, job.name, backup_dir, job.offsite_targets)

    apply_retention_days(
        global_cfg.backup_root,
        instance.name,
        "binlog",
        int(global_cfg.default_retention_days),
    )

    logger.info("Binlog backup completed", extra={"job": job.name, "backup_dir": backup_dir})

