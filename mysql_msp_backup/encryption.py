from __future__ import annotations

import os
from typing import List

from .logging_utils import get_logger
from .shell_utils import run


def build_xtrabackup_encryption_args(options: dict) -> List[str]:
    """
    Return extra xtrabackup/mariadb-backup CLI args for built-in AES256 encryption.
    """
    if not options.get("use_xtra_encryption"):
        return []

    env_key_var = options.get("xtra_key_env", "XTRABACKUP_ENCRYPTION_KEY")
    key = os.getenv(env_key_var)
    if not key:
        raise RuntimeError(f"xtrabackup encryption requested but env var {env_key_var} is not set")

    algo = options.get("xtra_encrypt_algo", "AES256")
    return [
        f"--encrypt={algo}",
        "--encrypt-key=" + key,
        "--encrypt-key-file=-",  # be explicit that we read from stdin if desired
    ]


def gpg_encrypt_directory(src_dir: str, output_path: str, recipient: str) -> None:
    """
    Tar+gzip a directory and encrypt it with gpg to `output_path`.
    """
    logger = get_logger()
    logger.info(
        "Encrypting directory with gpg",
        extra={"src_dir": src_dir, "output_path": output_path, "recipient": recipient},
    )
    # tar -C src_dir . | gzip | gpg -e -r recipient -o output_path
    cmd = [
        "bash",
        "-c",
        f"tar -C {src_dir} . | gzip | gpg -e -r {recipient} -o {output_path}",
    ]
    run(cmd, check=True)

