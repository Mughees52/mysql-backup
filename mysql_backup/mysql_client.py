from typing import Optional, Tuple

import pymysql

from .config import InstanceConfig
from .logging_utils import get_logger


def get_connection(instance: InstanceConfig) -> pymysql.connections.Connection:
    logger = get_logger()
    kwargs = {
        "host": instance.host,
        "port": instance.port,
        "user": instance.user,
        "password": instance.password,
        "charset": "utf8mb4",
        "cursorclass": pymysql.cursors.DictCursor,
    }
    if instance.socket:
        kwargs["unix_socket"] = instance.socket
    logger.debug("Connecting to MySQL", extra={"host": instance.host, "port": instance.port})
    return pymysql.connect(**kwargs)  # type: ignore[arg-type]


def estimate_database_size_bytes(instance: InstanceConfig) -> int:
    """
    Approximate total size of all tables for space estimation.
    """
    logger = get_logger()
    total = 0
    with get_connection(instance) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT IFNULL(SUM(data_length + index_length), 0) AS total_bytes
                FROM information_schema.tables
                WHERE table_schema NOT IN ('mysql','performance_schema','information_schema','sys')
                """
            )
            row = cur.fetchone() or {}
            total = int(row.get("total_bytes") or 0)
    logger.info("Estimated database size", extra={"instance": instance.name, "bytes": total})
    return total


def get_master_status(instance: InstanceConfig) -> Tuple[str, int]:
    """
    Return (binlog_file, position) from SHOW MASTER STATUS.
    """
    logger = get_logger()
    with get_connection(instance) as conn:
        with conn.cursor() as cur:
            cur.execute("SHOW MASTER STATUS")
            row = cur.fetchone()
            if not row:
                raise RuntimeError("SHOW MASTER STATUS returned no rows")
            file_name = row.get("File") or row.get("Log_name")
            pos = row.get("Position") or row.get("Pos")
            if not file_name or pos is None:
                raise RuntimeError("Could not parse SHOW MASTER STATUS output")
    logger.info(
        "Master status",
        extra={"instance": instance.name, "file": file_name, "position": int(pos)},
    )
    return str(file_name), int(pos)


def set_pxc_desync(instance: InstanceConfig, desync: bool) -> None:
    """
    Toggle wsrep_desync for PXC nodes.
    """
    if not instance.pxc:
        return
    logger = get_logger()
    with get_connection(instance) as conn:
        with conn.cursor() as cur:
            cur.execute("SET GLOBAL wsrep_desync = %s", ("ON" if desync else "OFF",))
        conn.commit()
    logger.info(
        "PXC desync toggled",
        extra={"instance": instance.name, "desync": desync},
    )

