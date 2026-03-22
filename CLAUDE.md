# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**mysql-backup** is a Python 3 backup suite for MySQL/MariaDB. It orchestrates logical (mydumper), physical (xtrabackup/mariadb-backup), and binlog (mysqlbinlog) backups with encryption, deduplication, offsite storage, and GASCAN-compliant retention policies.

Two package namespaces exist: `mysql_backup/` (primary) and `mysql_msp_backup/` (mirror, for managed service provider deployments). Both share the same module structure.

## Installation & Setup

```bash
# From source (development)
pip install .

# From GitHub
pip install "git+https://github.com/Mughees52/mysql-backup.git"

# RPM build (RHEL/Alma/Rocky)
rpmbuild -ba mysql-backup.spec
```

Requires Python 3.9+ and external binaries: `mydumper`, `xtrabackup`/`mariadb-backup`, `mysqlbinlog`, `gpg`, `rsync`, and optionally `aws`, `gsutil`, `az`.

## CLI Entry Points

```bash
mysql_backup_precheck                     # validate config, binaries, connectivity, disk space
mysql_backup_precheck --job logical-daily # precheck for a specific job

mysql_backup_driver --list-jobs           # list all configured jobs
mysql_backup_driver --validate-config     # parse and validate config only
mysql_backup_driver --self-test           # connectivity + environment test
mysql_backup_driver --dry-run             # simulate without writing
mysql_backup_driver --job logical-daily   # run a specific job by name
mysql_backup_driver --type physical       # run all jobs of a given type
```

## Architecture

### Flow

```
driver.py (CLI)
  → acquire fcntl lock file (prevent concurrent runs)
  → parse YAML config (config.py)
  → for each selected job:
      → replica/read-only gate check (mysql_client.py)
      → disk space check (checks.py, default 2.5× DB size)
      → kill long-running queries if configured (mysql_client.py)
      → execute job:
          LOGICAL  → backup_logical.py  (mydumper)
          PHYSICAL → backup_physical.py (xtrabackup/mariadb-backup)
          BINLOG   → backup_binlog.py   (mysqlbinlog streaming)
      → push offsite (storage_remote.py: S3/rsync/GCS/Azure)
      → apply retention (storage_local.py: daily + weekly tiers)
      → check graceful-stop sentinel file
  → release lock
```

### Key Modules

| Module | Role |
|--------|------|
| `config.py` | YAML parsing into dataclasses: `GlobalConfig`, `InstanceConfig`, `JobConfig`, `StorageTargetConfig` |
| `driver.py` | Orchestration, lock file, job selection, graceful stop |
| `precheck.py` | Pre-flight validation CLI |
| `backup_logical.py` | mydumper wrapper with incremental, compression, FTWRL guardian, GPG |
| `backup_physical.py` | xtrabackup/mariadb-backup: full/incremental cycle, PXC desync, AES256 encrypt, prepare, verify |
| `backup_binlog.py` | Continuous binlog streaming, position state file, disk guard |
| `storage_local.py` | Timestamp-based directory naming, daily + weekly retention enforcement |
| `storage_remote.py` | Offsite push via aws/gsutil/az/rsync CLI; failures are logged but non-fatal |
| `mysql_client.py` | pymysql wrapper: connectivity, size estimation, replica/read-only status, query killing, PXC desync |
| `encryption.py` | xtrabackup AES256 key resolution (file/env/literal) and GPG directory encryption |
| `dedup.py` | rsync `--link-dest` hard-linking for deduplication |
| `checks.py` | Disk space validation (2.5× factor) |
| `shell_utils.py` | Shell execution with retry/backoff |

### Configuration Structure

All behaviour is driven by a YAML config (see `config.yaml` for a full example). The hierarchy is:

```yaml
global:            # disk_space_factor, lock_file, log_file, graceful_stop_file
instances:         # per-host MySQL connection settings, replica_only, read_only_only, kill_long_queries
jobs:              # type (logical/physical/binlog), instance ref, backup_dir, retention_days, weekly_retention_weeks, storage targets
```

## Important Constraints

- Physical backups require `xtrabackup`/`mariadb-backup` to be installed and compatible with the server version (MySQL 8 requires Percona XtraBackup 8.x).
- The `mysql_msp_backup/` package is a structural mirror of `mysql_backup/`; changes to core logic typically need to be applied to both.
- Offsite upload failures are intentionally non-fatal — check logs rather than relying on exit codes for upload status.
- Disk space check uses a 2.5× multiplier by default (`disk_space_factor` in global config); this matches GASCAN sizing requirements.
