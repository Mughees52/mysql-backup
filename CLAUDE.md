# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**mysql-backup** is a Python 3 backup suite for MySQL/MariaDB. It orchestrates logical (mydumper), physical (xtrabackup/mariadb-backup), and binlog (mysqlbinlog) backups with encryption, deduplication, offsite storage, and GASCAN-compliant retention policies.

Two package namespaces exist: `mysql_backup/` (primary) and `mysql_msp_backup/` (mirror, for managed service provider deployments). Both must be kept in sync — any logic change applies to both.

## Dependencies

- Python 3.9+
- `PyYAML`, `pymysql`, `cryptography`, `python-dateutil`, `croniter` (all in `pyproject.toml`)
- External binaries: `mydumper`, `xtrabackup`/`mariadb-backup`, `mysqlbinlog`, `gpg`, `rsync`, optionally `aws`, `gsutil`, `az`

## Installation & Deployment

```bash
# From source (development)
pip install .

# From GitHub
pip install "git+https://github.com/Mughees52/mysql-backup.git"

# RPM build (RHEL/Alma/Rocky)
rpmbuild -ba mysql-backup.spec
```

**Live deployment:** Multipass VM `mysql-box`, venv at `/root/mysql-backup-venv`, config at `/root/.config/mysql-backup/config.yml`.

To deploy local changes to the live server:
```bash
cd "/Users/mugheesahmed/workspace/Mughees scripts"
tar czf /tmp/mysql-backup-deploy.tar.gz --exclude='.git' --exclude='*.pyc' --exclude='__pycache__' .
multipass transfer /tmp/mysql-backup-deploy.tar.gz mysql-box:/tmp/mysql-backup-deploy.tar.gz
multipass exec mysql-box -- sudo bash -c 'rm -rf /tmp/mysql-backup-src && mkdir -p /tmp/mysql-backup-src && tar xzf /tmp/mysql-backup-deploy.tar.gz -C /tmp/mysql-backup-src && source /root/mysql-backup-venv/bin/activate && pip install -q --force-reinstall /tmp/mysql-backup-src/'
```

## CLI Entry Points

```bash
mysql_backup_precheck                        # validate config, binaries, connectivity, disk space
mysql_backup_precheck --job logical-daily    # precheck a specific job

mysql_backup_driver --list-jobs              # list all configured jobs
mysql_backup_driver --validate-config        # parse and validate config only
mysql_backup_driver --self-test              # connectivity + environment test
mysql_backup_driver --run-scheduled          # run jobs whose schedule_hint is due now (used by cron)
mysql_backup_driver --run-scheduled --dry-run  # show which jobs would fire without executing
mysql_backup_driver --job logical-daily      # run a specific job by name
mysql_backup_driver --type physical          # run all jobs of a given type
```

## Scheduling

The cron entry on `mysql-box` is `/etc/cron.d/mysql-backup`:
```
* * * * * root /root/mysql-backup-venv/bin/mysql_backup_driver --run-scheduled >> /var/log/mysql-backup/cron.log 2>&1
```

`--run-scheduled` uses `croniter` to evaluate each job's `schedule_hint` against the current time and runs only due jobs. One cron line replaces all per-job entries. Schedules are configured entirely in `config.yml`.

## Architecture

### Job selection flow

```
driver.py (CLI)
  → parse args
  → load YAML config (config.py)
  → select jobs:
      --job NAME        → filter by name
      --type TYPE       → filter by type
      --run-scheduled   → filter to jobs whose schedule_hint is due now (croniter)
      (no filter)       → all jobs
  → acquire fcntl lock file (prevent concurrent runs)
  → for each selected job:
      → graceful-stop sentinel check
      → replica/read-only gate (mysql_client.py)
      → disk space check: 2.5× DB size + 512MB (checks.py)
      → kill long-running queries if configured (mysql_client.py)
      → execute:
          LOGICAL  → backup_logical.py   (mydumper)
          PHYSICAL → backup_physical.py  (xtrabackup/mariadb-backup)
          BINLOG   → backup_binlog.py    (mysqlbinlog --read-from-remote-server)
      → push offsite (storage_remote.py: S3/rsync/GCS/Azure — failures non-fatal)
      → apply retention (storage_local.py: daily + weekly tiers)
  → release lock
```

### Key Modules

| Module | Role |
|--------|------|
| `config.py` | YAML parsing into dataclasses: `GlobalConfig`, `InstanceConfig`, `JobConfig`, `StorageTargetConfig` |
| `driver.py` | Orchestration, lock file, job selection, `--run-scheduled` scheduling, graceful stop |
| `precheck.py` | Pre-flight validation CLI |
| `backup_logical.py` | mydumper: compression, triggers, less-locking, FTWRL guardian, incremental |
| `backup_physical.py` | xtrabackup/mariadb-backup: full/incremental, `--defaults-file` credentials, decrypt→prepare for AES256, PXC desync, verify |
| `backup_binlog.py` | `mysqlbinlog --read-from-remote-server` streaming, position state file, disk % guard |
| `storage_local.py` | Timestamp-based directory naming, daily + weekly retention enforcement |
| `storage_remote.py` | Offsite push via aws/gsutil/az/rsync CLI; failures logged but non-fatal |
| `mysql_client.py` | pymysql wrapper: connectivity, size estimation, replica/read-only status, query killing, PXC desync |
| `encryption.py` | xtrabackup AES256 key resolution (file/env/literal), GPG directory encryption |
| `dedup.py` | rsync `--link-dest` hard-linking for snapshot deduplication |
| `checks.py` | Disk space validation (2.5× factor + 512MB buffer) |
| `shell_utils.py` | Shell execution with retry/exponential backoff |

## Known Behaviours & Constraints

### Physical backup encryption (two-step prepare)
AES256-encrypted physical backups require two steps after backup:
1. `xtrabackup --decrypt=AES256 --encrypt-key-file=... --target-dir=...` — decrypts `.xbcrypt` files in-place
2. `xtrabackup --prepare --target-dir=...` — applies redo log

`backup_physical.py` does this automatically when `use_xtra_encryption: true` and `prepare_after_backup: true`. Do **not** pass `--encrypt` to the prepare step — it must be `--decrypt`.

### xtrabackup credentials file (`defaults_file`)
When `defaults_file` is set in a physical job's `backup_options`, the path is passed as `--defaults-file=<path>` (must be the first argument after the binary) and `--password` is omitted from the command line. The file uses `[xtrabackup]` section format:
```ini
[xtrabackup]
user=backup
password=...
host=localhost
port=3306
```

### Binlog streaming
`backup_binlog.py` always passes `--read-from-remote-server` to mysqlbinlog. Without it, mysqlbinlog tries to read a local file. The backup user needs `REPLICATION SLAVE` privilege. MySQL 8.0 uses `binlog.XXXXXX` filenames (not `mysql-bin.XXXXXX`).

### MySQL user required grants
```sql
GRANT SELECT, RELOAD, PROCESS, LOCK TABLES, REPLICATION CLIENT, SHOW VIEW, BACKUP_ADMIN,
      REPLICATION SLAVE ON *.* TO 'backup'@'localhost';
```

### Dual-package mirror
`mysql_msp_backup/` mirrors `mysql_backup/` exactly. Every code change must be applied to both packages.

### Disk space requirement
2.5× estimated DB size + 512MB must be free on the backup volume before any job runs. The check happens in `backup_logical.py` and `backup_physical.py` before any backup tool is invoked.

## Configuration Structure

```yaml
global:            # backup_root, log_dir, tmp_dir, disk_space_factor, retention, graceful_stop_file
instances:         # MySQL connection: host, port, user, password_env, replica_only, read_only_only, pxc
jobs:              # type (logical/physical/binlog), instance, schedule_hint, backup_options, offsite_targets
storage:           # s3 / rsync / gcs / azure target definitions
```

Key `backup_options` fields by type:

| Type | Key fields |
|------|-----------|
| physical | `tool`, `backup_mode`, `defaults_file`, `use_xtra_encryption`, `xtra_key_file`, `prepare_after_backup`, `backup_copies` |
| logical | `mydumper_path`, `threads`, `compress`, `dump_triggers`, `less_locking` |
| binlog | `mysqlbinlog_path`, `binlog_file` (first run only), `min_free_disk_pct`, `binlog_retention_days` |

## Documentation Rule

After every successful code change and test, update **both** `README.md` (end-user docs) and this `CLAUDE.md` (developer context) before considering the task complete.
