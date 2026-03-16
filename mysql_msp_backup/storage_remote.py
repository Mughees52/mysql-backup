from __future__ import annotations

import os
from typing import Dict

from .config import BackupConfig, StorageTargetConfig
from .logging_utils import get_logger
from .shell_utils import run_with_retries


def _push_s3(target: StorageTargetConfig, local_path: str) -> None:
    logger = get_logger()
    opts = target.options
    bucket = opts["bucket"]
    prefix = opts.get("prefix", "")
    dest = f"s3://{bucket}/{prefix}".rstrip("/") + "/"
    logger.info("Uploading backup to S3", extra={"local": local_path, "dest": dest})
    run_with_retries(["aws", "s3", "cp", "--recursive", local_path, dest], check=True)


def _push_rsync(target: StorageTargetConfig, local_path: str) -> None:
    logger = get_logger()
    opts = target.options
    dest = opts["target"]
    logger.info("Uploading backup with rsync", extra={"local": local_path, "dest": dest})
    run_with_retries(["rsync", "-a", local_path.rstrip("/") + "/", dest.rstrip("/") + "/"], check=True)


def _push_gcs(target: StorageTargetConfig, local_path: str) -> None:
    logger = get_logger()
    opts = target.options
    bucket = opts["bucket"]
    prefix = opts.get("prefix", "")
    dest = f"gs://{bucket}/{prefix}".rstrip("/") + "/"
    logger.info("Uploading backup to GCS", extra={"local": local_path, "dest": dest})
    run_with_retries(["gsutil", "-m", "cp", "-r", local_path, dest], check=True)


def push_offsite(cfg: BackupConfig, job_name: str, local_path: str, target_names: list[str]) -> None:
    logger = get_logger()
    for name in target_names:
        target = cfg.storage_targets.get(name)
        if not target:
            logger.warning("Unknown storage target", extra={"target": name})
            continue
        try:
            if target.type == "s3":
                _push_s3(target, local_path)
            elif target.type == "rsync":
                _push_rsync(target, local_path)
            elif target.type == "gcs":
                _push_gcs(target, local_path)
            else:
                logger.warning("Unsupported storage target type", extra={"type": target.type})
        except Exception:
            logger.exception("Offsite upload failed", extra={"target": name, "job": job_name})

