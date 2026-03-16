import os
import shutil
from typing import Optional

from .logging_utils import get_logger
from .shell_utils import run


def link_dest_snapshot(previous_dir: Optional[str], new_dir: str) -> None:
    """
    If previous_dir is given, use rsync --link-dest to populate new_dir with
    hard links to unchanged files from previous_dir.
    """
    logger = get_logger()
    if not previous_dir or not os.path.isdir(previous_dir):
        return

    parent = os.path.dirname(new_dir.rstrip("/"))
    os.makedirs(parent, exist_ok=True)

    logger.info(
        "Creating deduplicated snapshot",
        extra={"previous_dir": previous_dir, "new_dir": new_dir},
    )
    # rsync -a --delete --link-dest=previous_dir previous_dir/ new_dir/
    run(
        [
            "rsync",
            "-a",
            "--delete",
            f"--link-dest={previous_dir}",
            previous_dir.rstrip("/") + "/",
            new_dir.rstrip("/") + "/",
        ],
        check=True,
    )

