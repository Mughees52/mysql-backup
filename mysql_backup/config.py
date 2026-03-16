import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import yaml


@dataclass
class GlobalConfig:
    backup_root: str
    log_dir: str
    tmp_dir: str
    default_encryption: str = "none"
    default_retention_days: int = 7
    default_timeout_seconds: int = 3600


@dataclass
class InstanceConfig:
    name: str
    host: str = "localhost"
    port: int = 3306
    user: str = "root"
    password: Optional[str] = None
    password_env: Optional[str] = None
    socket: Optional[str] = None
    pxc: bool = False
    pxc_desync: bool = False
    pxc_cluster_name: Optional[str] = None


@dataclass
class JobConfig:
    name: str
    instance: str
    type: str  # logical|physical|binlog
    schedule_hint: Optional[str] = None
    backup_options: Dict[str, Any] = field(default_factory=dict)
    encryption: Optional[str] = None
    dedup: bool = False
    offsite_targets: List[str] = field(default_factory=list)


@dataclass
class StorageTargetConfig:
    name: str
    type: str  # s3|rsync|gcs
    options: Dict[str, Any] = field(default_factory=dict)


@dataclass
class BackupConfig:
    global_config: GlobalConfig
    instances: Dict[str, InstanceConfig]
    jobs: Dict[str, JobConfig]
    storage_targets: Dict[str, StorageTargetConfig]


class ConfigError(Exception):
    pass


def _load_yaml(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def _resolve_password(instance_raw: Dict[str, Any]) -> Optional[str]:
    pwd = instance_raw.get("password")
    pwd_env = instance_raw.get("password_env")
    if pwd_env:
        env_val = os.getenv(pwd_env)
        if env_val:
            return env_val
    return pwd


def load_config(path: str) -> BackupConfig:
    raw = _load_yaml(path)

    global_raw = raw.get("global") or {}
    try:
        global_cfg = GlobalConfig(
            backup_root=global_raw["backup_root"],
            log_dir=global_raw["log_dir"],
            tmp_dir=global_raw.get("tmp_dir", "/tmp/mysql-backup"),
            default_encryption=global_raw.get("default_encryption", "none"),
            default_retention_days=int(global_raw.get("default_retention_days", 7)),
            default_timeout_seconds=int(global_raw.get("default_timeout_seconds", 3600)),
        )
    except KeyError as exc:
        raise ConfigError(f"Missing required global config key: {exc}") from exc

    instances_raw = raw.get("instances") or []
    instances: Dict[str, InstanceConfig] = {}
    for inst in instances_raw:
        name = inst["name"]
        instances[name] = InstanceConfig(
            name=name,
            host=inst.get("host", "localhost"),
            port=int(inst.get("port", 3306)),
            user=inst.get("user", "root"),
            password=_resolve_password(inst),
            password_env=inst.get("password_env"),
            socket=inst.get("socket"),
            pxc=bool(inst.get("pxc", False)),
            pxc_desync=bool(inst.get("pxc_desync", False)),
            pxc_cluster_name=inst.get("pxc_cluster_name"),
        )

    jobs_raw = raw.get("jobs") or []
    jobs: Dict[str, JobConfig] = {}
    for job in jobs_raw:
        name = job["name"]
        jobs[name] = JobConfig(
            name=name,
            instance=job["instance"],
            type=job["type"],
            schedule_hint=job.get("schedule_hint"),
            backup_options=job.get("backup_options", {}) or {},
            encryption=job.get("encryption"),
            dedup=bool(job.get("dedup", False)),
            offsite_targets=job.get("offsite_targets") or [],
        )

    storage_raw = raw.get("storage") or []
    storage_targets: Dict[str, StorageTargetConfig] = {}
    for st in storage_raw:
        name = st["name"]
        storage_targets[name] = StorageTargetConfig(
            name=name,
            type=st["type"],
            options=st.get("options", {}) or {},
        )

    return BackupConfig(
        global_config=global_cfg,
        instances=instances,
        jobs=jobs,
        storage_targets=storage_targets,
    )


def validate_config(cfg: BackupConfig) -> None:
    """
    Basic structural validation of the configuration.
    """
    # Instances must be unique and non-empty
    if not cfg.instances:
        raise ConfigError("No instances defined in configuration")

    for job in cfg.jobs.values():
        if job.instance not in cfg.instances:
            raise ConfigError(f"Job {job.name} references unknown instance {job.instance}")
        if job.type not in {"logical", "physical", "binlog"}:
            raise ConfigError(f"Job {job.name} has invalid type {job.type}")
        for target in job.offsite_targets:
            if target not in cfg.storage_targets:
                raise ConfigError(f"Job {job.name} references unknown storage target {target}")

    # Simple check that directories are writable/creatable
    for path_key, path in [
        ("backup_root", cfg.global_config.backup_root),
        ("log_dir", cfg.global_config.log_dir),
        ("tmp_dir", cfg.global_config.tmp_dir),
    ]:
        base = os.path.dirname(path) or "."
        if not os.path.exists(base):
            raise ConfigError(f"Base directory for {path_key} does not exist: {base}")

