import logging
import os
from logging.handlers import RotatingFileHandler
from typing import Optional


def setup_logging(log_dir: str, name: str = "mysql_msp_backup", level: int = logging.INFO) -> logging.Logger:
    os.makedirs(log_dir, exist_ok=True)
    logger = logging.getLogger(name)
    logger.setLevel(level)

    if not logger.handlers:
        log_path = os.path.join(log_dir, f"{name}.log")
        fh = RotatingFileHandler(log_path, maxBytes=10 * 1024 * 1024, backupCount=5)
        fmt = logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        fh.setFormatter(fmt)
        logger.addHandler(fh)

        sh = logging.StreamHandler()
        sh.setFormatter(fmt)
        logger.addHandler(sh)

    return logger


def get_logger(name: Optional[str] = None) -> logging.Logger:
    return logging.getLogger(name or "mysql_msp_backup")

