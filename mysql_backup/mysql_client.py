import os
from typing import List, Optional, Tuple

import pymysql

from .config import InstanceConfig
from .logging_utils import get_logger


def get_connection(instance: InstanceConfig) -> pymysql.connections.Connection:
    logger = get_logger()
    kwargs = {
        "host": instance.host,
        "port": instance.port,
        "user": instance.user,
        "charset": "utf8mb4",
        "cursorclass": pymysql.cursors.DictCursor,
    }
    if instance.password is not None:
        kwargs["password"] = instance.password
    else:
        kwargs["read_default_file"] = os.path.expanduser("~/.my.cnf")
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


def check_is_replica(instance: InstanceConfig) -> bool:
    """Return True if the instance is currently running as a replication replica."""
    with get_connection(instance) as conn:
        with conn.cursor() as cur:
            cur.execute("SHOW REPLICA STATUS")
            row = cur.fetchone()
            if row is None:
                cur.execute("SHOW SLAVE STATUS")
                row = cur.fetchone()
            if not row:
                return False
            sql_running = row.get("Replica_SQL_Running") or row.get("Slave_SQL_Running") or ""
            io_running = row.get("Replica_IO_Running") or row.get("Slave_IO_Running") or ""
            return sql_running.upper() == "YES" and io_running.upper() == "YES"


def check_is_read_only(instance: InstanceConfig) -> bool:
    """Return True if the instance has read_only or super_read_only enabled."""
    with get_connection(instance) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT @@read_only AS ro, @@super_read_only AS sro")
            row = cur.fetchone() or {}
            return bool(row.get("ro")) or bool(row.get("sro"))


def kill_long_queries(
    instance: InstanceConfig,
    threshold_seconds: int = 10,
    query_type: str = "select",
) -> List[int]:
    """
    Kill queries running longer than threshold_seconds.

    query_type: "select" kills only SELECT queries; "all" kills any query
    (excluding replication and system threads).

    Returns list of killed process IDs.
    """
    logger = get_logger()
    killed: List[int] = []
    with get_connection(instance) as conn:
        with conn.cursor() as cur:
            cur.execute("SHOW FULL PROCESSLIST")
            rows = cur.fetchall() or []
        for row in rows:
            pid = row.get("Id")
            command = (row.get("Command") or "").upper()
            info = (row.get("Info") or "").strip().upper()
            time_val = int(row.get("Time") or 0)

            if command in ("BINLOG DUMP", "BINLOG DUMP GTID", "SLAVE", "SYSTEM USER", "DAEMON"):
                continue
            if time_val < threshold_seconds:
                continue
            if query_type.lower() == "select" and not info.startswith("SELECT"):
                continue

            try:
                with conn.cursor() as cur:
                    cur.execute(f"KILL QUERY {pid}")
                logger.info("Killed long-running query", extra={"pid": pid, "time": time_val, "info": info[:80]})
                killed.append(pid)
            except Exception as exc:
                logger.warning("Failed to kill query", extra={"pid": pid, "error": str(exc)})
    return killed


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

