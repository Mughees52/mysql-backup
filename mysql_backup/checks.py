import os
import shutil
from typing import Optional

from .logging_utils import get_logger


def check_disk_space(path: str, required_bytes: int) -> bool:
    """
    Return True if the filesystem containing `path` has at least `required_bytes` free.
    """
    logger = get_logger()
    os.makedirs(path, exist_ok=True)
    usage = shutil.disk_usage(path)
    ok = usage.free >= required_bytes
    logger.info(
        "Disk space check",
        extra={
            "path": path,
            "required_bytes": required_bytes,
            "free_bytes": usage.free,
            "ok": ok,
        },
    )
    return ok


def estimate_required_bytes(approx_db_bytes: int, factor: float = 2.5, extra_bytes: int = 512 * 1024 * 1024) -> int:
    """
    Estimate required space as factor * approx_db_bytes + extra_bytes.

    The default factor of 2.5 matches the GASCAN recommendation (backup disk
    should be at least 2.5x the size of the MySQL data directory).
    """
    return int(approx_db_bytes * factor) + extra_bytes

