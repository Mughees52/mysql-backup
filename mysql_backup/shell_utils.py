import subprocess
import time
from typing import List, Mapping, Optional

from .logging_utils import get_logger


class ShellError(RuntimeError):
    def __init__(self, cmd: List[str], returncode: int, stdout: str, stderr: str):
        super().__init__(f"Command failed with exit code {returncode}: {' '.join(cmd)}")
        self.cmd = cmd
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def run(
    cmd: List[str],
    env: Optional[Mapping[str, str]] = None,
    cwd: Optional[str] = None,
    check: bool = True,
    timeout: Optional[float] = None,
) -> subprocess.CompletedProcess:
    logger = get_logger()
    logger.debug("Executing command", extra={"cmd": cmd, "cwd": cwd})
    proc = subprocess.run(
        cmd,
        env=env,  # type: ignore[arg-type]
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
    )
    if check and proc.returncode != 0:
        logger.error(
            "Command failed",
            extra={
                "cmd": cmd,
                "returncode": proc.returncode,
                "stdout": proc.stdout,
                "stderr": proc.stderr,
            },
        )
        raise ShellError(cmd, proc.returncode, proc.stdout, proc.stderr)
    return proc


def run_with_retries(
    cmd: List[str],
    env: Optional[Mapping[str, str]] = None,
    cwd: Optional[str] = None,
    check: bool = True,
    timeout: Optional[float] = None,
    retries: int = 3,
    backoff_seconds: float = 2.0,
) -> subprocess.CompletedProcess:
    """
    Run a command with simple retry and exponential backoff for transient failures.
    """
    logger = get_logger()
    attempt = 0
    last_exc: Optional[Exception] = None
    while attempt <= retries:
        try:
            if attempt > 0:
                logger.warning(
                    "Retrying command",
                    extra={"cmd": cmd, "attempt": attempt},
                )
            return run(cmd, env=env, cwd=cwd, check=check, timeout=timeout)
        except (ShellError, subprocess.TimeoutExpired) as exc:  # type: ignore[misc]
            last_exc = exc
            if attempt == retries:
                logger.error(
                    "Command failed after retries",
                    extra={"cmd": cmd, "retries": retries},
                )
                raise
            sleep_for = backoff_seconds * (2**attempt)
            time.sleep(sleep_for)
            attempt += 1

    if last_exc:
        raise last_exc
    raise RuntimeError("run_with_retries failed without raising a specific error")

