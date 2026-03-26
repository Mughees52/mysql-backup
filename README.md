# mysql-backup

Python 3 backup suite for MySQL/MariaDB providing:

- **Logical backups** via `mydumper` — full and incremental, trigger dump, less-locking, FTWRL guardian
- **Physical backups** via `xtrabackup` / `mariadb-backup` — full and incremental, configurable full-backup cycle, AES-256 encryption
- **Binlog backups** via `mysqlbinlog` — continuous remote streaming with position tracking
- **Offsite storage** — S3, rsync, GCS, Azure Blob Storage
- **Retention** — configurable daily + weekly tiers
- **Safety gates** — replica-aware (`replica_only`, `read_only_only`), disk-space checks (2.5× by default)
- **PXC support** — automatic desync/resync around physical backups on Galera/XtraDB Cluster nodes
- **Operational** — lock file (prevents concurrent runs), graceful stop, kill long-running queries before backup

> **Documentation:** All guides, runbooks, and test records live in the [`docs/`](docs/) folder.

---

## Table of Contents

1. [Prerequisites](#1-prerequisites)
2. [Installation](#2-installation)
3. [MySQL user setup](#3-mysql-user-setup)
4. [Configuration](#4-configuration)
5. [Running backups](#5-running-backups)
6. [Scheduling with cron](#6-scheduling-with-cron)
7. [How-to guides](#7-how-to-guides)
8. [Configuration reference](#8-configuration-reference)
9. [Troubleshooting](#9-troubleshooting)

---

## 1. Prerequisites

**System tools** — install before using:

| Tool | Required for |
|------|-------------|
| `mydumper` | Logical backups |
| `xtrabackup` or `mariadb-backup` | Physical backups |
| `mysqlbinlog` | Binlog backups |
| `gpg` | GPG encryption (optional) |
| `rsync` | Deduplication + rsync offsite |
| `aws` CLI | S3 offsite (optional) |
| `gsutil` | GCS offsite (optional) |
| `az` CLI | Azure Blob offsite (optional) |

**Python:** 3.9 or later.

**Disk sizing:** The backup host must have at least **2.5× the size of the MySQL data directory free** on the backup volume. The driver enforces this before every job (configurable via `disk_space_factor`).

**xtrabackup version compatibility:** xtrabackup 8.x is required for MySQL 8.x. xtrabackup 2.4.x is for MySQL 5.7 only. Running a mismatched version will produce `unsupported server version` errors.

[↑ Back to top](#table-of-contents)

---

## 2. Installation

Run the driver as **root** so it can write to `/var/backups/mysql` and `/var/log/mysql-backup`. On Ubuntu/Debian (PEP 668), install into a virtualenv:

### Option A — from GitHub (recommended)

```bash
sudo -i
python3 -m venv /root/mysql-backup-venv
source /root/mysql-backup-venv/bin/activate
pip install --upgrade pip
pip install "git+https://github.com/Mughees52/mysql-backup.git"
```

The CLIs are installed at `/root/mysql-backup-venv/bin/mysql_backup_driver` and `mysql_backup_precheck`.

To **upgrade** an existing install to the latest `main`:

```bash
source /root/mysql-backup-venv/bin/activate
pip install --force-reinstall --no-cache-dir "git+https://github.com/Mughees52/mysql-backup.git@main"
```

### Option B — from source

```bash
sudo -i
cd /opt/mysql-backup        # or wherever you cloned the repo
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install .
```

### Option C — RPM (RHEL / Alma / Rocky)

```bash
VERSION=0.1.0
tar czf mysql-backup-${VERSION}.tar.gz mysql-backup-${VERSION}/
mv mysql-backup-${VERSION}.tar.gz ~/rpmbuild/SOURCES/
cp mysql-backup.spec ~/rpmbuild/SPECS/
rpmbuild -ba ~/rpmbuild/SPECS/mysql-backup.spec
sudo dnf install ~/rpmbuild/RPMS/noarch/mysql-backup-${VERSION}-1*.rpm
```

> **Note:** Do not run `sudo -i` again after activating the virtualenv — it resets `PATH`. If you get `command not found`, re-run `source /root/mysql-backup-venv/bin/activate` or use the full path `/root/mysql-backup-venv/bin/mysql_backup_driver`.

[↑ Back to top](#table-of-contents)

---

## 3. MySQL user setup

Create a dedicated backup user on each MySQL server. The required grants depend on which backup types you use:

```sql
-- Minimum for logical (mydumper) and physical (xtrabackup) backups:
CREATE USER IF NOT EXISTS 'backup'@'localhost' IDENTIFIED BY 'strong_password_here';
GRANT SELECT, RELOAD, PROCESS, LOCK TABLES, REPLICATION CLIENT, SHOW VIEW, BACKUP_ADMIN
  ON *.* TO 'backup'@'localhost';

-- Additional grant required for binlog streaming (mysqlbinlog --read-from-remote-server):
GRANT REPLICATION SLAVE ON *.* TO 'backup'@'localhost';

FLUSH PRIVILEGES;
```

**Why each grant is needed:**

| Grant | Used by |
|-------|---------|
| `SELECT` | Estimating database size, mydumper table dumps |
| `RELOAD` | `FLUSH TABLES WITH READ LOCK` (FTWRL) during physical backup |
| `PROCESS` | Listing and killing long-running queries |
| `LOCK TABLES` | Table-level locking during mydumper |
| `REPLICATION CLIENT` | `SHOW BINARY LOGS`, `SHOW MASTER STATUS` |
| `SHOW VIEW` | Dumping views with mydumper |
| `BACKUP_ADMIN` | xtrabackup on MySQL 8+ (`LOCK INSTANCE FOR BACKUP`) |
| `REPLICATION SLAVE` | `mysqlbinlog --read-from-remote-server` binlog streaming |

[↑ Back to top](#table-of-contents)

---

## 4. Configuration

### 4.1 Create the config directory

```bash
sudo -i
mkdir -p /root/.config/mysql-backup
```

### 4.2 Minimal config (logical backup only)

```yaml
# /root/.config/mysql-backup/config.yml
global:
  backup_root: /var/backups/mysql
  log_dir: /var/log/mysql-backup
  tmp_dir: /tmp/mysql-backup
  default_retention_days: 7

instances:
  - name: local-mysql
    host: localhost
    port: 3306
    user: root          # must match the user in ~/.my.cnf

jobs:
  - name: logical-daily
    instance: local-mysql
    type: logical
    schedule_hint: "0 2 * * *"
    backup_options:
      mydumper_path: /usr/bin/mydumper
      threads: 4
      compress: true
    offsite_targets: []

storage: []
```

When no `password` or `password_env` is set in the instance config, the driver reads credentials from `~/.my.cnf` automatically (via pymysql's `read_default_file`). Store your MySQL password there:

```bash
# /root/.my.cnf  (chmod 600)
[client]
user=root
password=your_password_here
host=localhost
socket=/var/run/mysqld/mysqld.sock
```

Then run without any environment variable:

```bash
mysql_backup_driver --job logical-daily
```

### 4.3 Full config example (logical + physical + binlog)

```yaml
global:
  backup_root: /var/backups/mysql
  log_dir: /var/log/mysql-backup
  tmp_dir: /tmp/mysql-backup
  default_encryption: none
  default_retention_days: 7
  weekly_retention_weeks: 4
  disk_space_factor: 2.5
  default_timeout_seconds: 3600
  graceful_stop_file: /root/.config/mysql-backup/GRACEFUL_STOP

instances:
  - name: prod-mysql1
    host: localhost
    port: 3306
    user: root            # credentials read from ~/.my.cnf

  - name: prod-replica1
    host: localhost
    port: 3306
    user: root            # credentials read from ~/.my.cnf
    replica_only: true          # skip backup if not currently an active replica
    read_only_only: false

  - name: prod-pxc1
    host: 10.0.1.10
    port: 3306
    user: root            # credentials read from ~/.my.cnf
    pxc: true
    pxc_desync: true
    pxc_cluster_name: prod-pxc

jobs:
  - name: logical-daily
    instance: prod-mysql1
    type: logical
    schedule_hint: "0 2 * * *"
    retention_days: 7
    weekly_retention_weeks: 4
    backup_options:
      mydumper_path: /usr/bin/mydumper
      threads: 8
      chunk_filesize: 64
      rows: 500000
      compress: true
      dump_triggers: true
      less_locking: true
    offsite_targets: ["s3-main", "rsync-dr"]

  - name: physical-daily
    instance: prod-mysql1
    type: physical
    schedule_hint: "0 1 * * *"
    retention_days: 7
    weekly_retention_weeks: 4
    backup_options:
      tool: xtrabackup
      xtrabackup_path: /usr/bin/xtrabackup
      backup_mode: full
      prepare_after_backup: true
      prepare_memory: 2G
      defaults_file: /root/.config/mysql-backup/xtrabackup.cnf  # xtrabackup reads credentials from here
      use_xtra_encryption: true
      xtra_key_file: /root/.secrets/xtrabackup.key
      xtra_encrypt_algo: AES256
      kill_long_queries: true
      kill_queries_timeout: 10
      backup_copies: 3
    encryption: xtrabackup_aes256
    dedup: true
    offsite_targets: ["s3-main", "rsync-dr"]

  - name: binlog-5min
    instance: prod-mysql1
    type: binlog
    schedule_hint: "*/5 * * * *"
    backup_options:
      mysqlbinlog_path: /usr/bin/mysqlbinlog
      binlog_file: binlog.000001    # first run only — check actual name with: SHOW BINARY LOGS;
      binlog_retention_days: 30
      min_free_disk_pct: 5.0
    offsite_targets: ["s3-main"]

storage:
  - name: s3-main
    type: s3
    options:
      bucket: my-mysql-backups
      prefix: prod/
      # storage_class: STANDARD_IA
      # kms_key_id: "arn:aws:kms:region:acct:key/uuid"

  - name: rsync-dr
    type: rsync
    options:
      target: backup@dr-host:/data/mysql-backups
      # ssh_key: /home/backup/.ssh/id_rsa
      # options: "-z"

  - name: gcs-dr
    type: gcs
    options:
      bucket: my-gcs-backups
      prefix: prod/

  - name: azure-dr
    type: azure
    options:
      container: mysql-backups
      account_name: myazurestorageaccount
      destination_path: prod/
      # sas_token: "sp=rw&st=..."
```

[↑ Back to top](#table-of-contents)

---

## 5. Running backups

### Validate config and run pre-checks

Always run `mysql_backup_precheck` before enabling cron to confirm binaries, connectivity, directories, and permissions are all in order:

```bash
export MYSQL_BACKUP_PASSWORD='s3cr3t'
source /root/mysql-backup-venv/bin/activate

# Check all jobs:
mysql_backup_precheck

# Check one job:
mysql_backup_precheck --job logical-daily

# Check all jobs for one instance:
mysql_backup_precheck --instance prod-mysql1
```

### Driver CLI reference

```bash
# List all configured jobs
mysql_backup_driver --list-jobs

# Validate config file syntax only (no MySQL connection)
mysql_backup_driver --validate-config

# Connectivity + environment smoke test
mysql_backup_driver --self-test

# Run a specific job
mysql_backup_driver --job logical-daily

# Run all jobs of a given type
mysql_backup_driver --type physical
mysql_backup_driver --type binlog

# Run only jobs whose schedule_hint is due right now (used by cron)
mysql_backup_driver --run-scheduled

# Dry run — shows what would happen without writing anything
mysql_backup_driver --run-scheduled --dry-run
mysql_backup_driver --job physical-daily --dry-run

# Use a non-default config file
mysql_backup_driver --config /etc/mysql-backup/prod.yml

# Use a non-default lock file (for multiple configs on same host)
mysql_backup_driver --config prod.yml --lock-file /tmp/prod.lock
```

[↑ Back to top](#table-of-contents)

---

## 6. Scheduling with cron

The driver has a `--run-scheduled` mode that reads each job's `schedule_hint` from `config.yml` and runs only the jobs that are due at the current minute. **One cron entry drives all your jobs** — the schedule lives in `config.yml`, not in crontab.

### How it works

Every minute, cron invokes `mysql_backup_driver --run-scheduled`. The driver:
1. Loads `config.yml`
2. For each job, evaluates its `schedule_hint` cron expression against the current time
3. Runs any job whose schedule was due within the last 60 seconds
4. Jobs without a `schedule_hint` are skipped (manual-only)

### Setup

Create `/etc/cron.d/mysql-backup` with a single entry:

```cron
# /etc/cron.d/mysql-backup
* * * * * root /root/mysql-backup-venv/bin/mysql_backup_driver --run-scheduled >> /var/log/mysql-backup/cron.log 2>&1
```

No password environment variable is needed. The driver reads credentials from `/root/.my.cnf` automatically.

To change when a job runs, edit `schedule_hint` in `config.yml` — no crontab changes needed.

### Example schedule configuration in config.yml

```yaml
jobs:
  - name: logical-daily
    schedule_hint: "0 2 * * *"      # 2:00am every day

  - name: physical-daily
    schedule_hint: "0 1 * * *"      # 1:00am every day

  - name: binlog-5min
    schedule_hint: "*/5 * * * *"    # every 5 minutes

  - name: manual-restore-test
    # no schedule_hint — only runs when called explicitly with --job
```

### Verifying the schedule

```bash
# See which jobs would run right now (no changes made)
mysql_backup_driver --run-scheduled --dry-run

# List all jobs and their configured schedules
mysql_backup_driver --list-jobs
```

> **"No jobs selected"** in the log is **expected** when `--run-scheduled` is invoked at a time when no job's `schedule_hint` is due. This happens every minute where no schedule matches — for example, `binlog-5min` (`*/5 * * * *`) will show "No jobs selected" for four out of every five minutes.

### Multiple configs on the same host

```cron
MYSQL_BACKUP_PASSWORD=s3cr3t

* * * * * root /root/mysql-backup-venv/bin/mysql_backup_driver --config /root/.config/mysql-backup/prod1.yml --lock-file /tmp/backup-prod1.lock --run-scheduled >> /var/log/mysql-backup/prod1-cron.log 2>&1
* * * * * root /root/mysql-backup-venv/bin/mysql_backup_driver --config /root/.config/mysql-backup/prod2.yml --lock-file /tmp/backup-prod2.lock --run-scheduled >> /var/log/mysql-backup/prod2-cron.log 2>&1
```

### Kubernetes CronJob

```yaml
apiVersion: batch/v1
kind: CronJob
metadata:
  name: mysql-backup
spec:
  schedule: "* * * * *"    # runs every minute; schedule_hint in config.yml controls which jobs fire
  jobTemplate:
    spec:
      template:
        spec:
          containers:
            - name: backup
              image: your-registry/mysql-backup:latest
              args: ["mysql_backup_driver", "--config", "/etc/backup/config.yml", "--run-scheduled"]
              env:
                - name: MYSQL_BACKUP_PASSWORD
                  valueFrom:
                    secretKeyRef:
                      name: mysql-backup-secret
                      key: password
              volumeMounts:
                - name: config
                  mountPath: /etc/backup
                - name: backup-storage
                  mountPath: /var/backups/mysql
          restartPolicy: OnFailure
          volumes:
            - name: config
              configMap:
                name: mysql-backup-config
            - name: backup-storage
              persistentVolumeClaim:
                claimName: mysql-backup-pvc
```

[↑ Back to top](#table-of-contents)

---

## 7. How-to guides

All guides are in the [`docs/`](docs/) folder:

| Guide | Description |
|-------|-------------|
| [Setup: AES-256 encryption](docs/setup-encryption.md) | Generate keys, credentials file, job config for encrypted physical backups |
| [Setup: Binlog backups](docs/setup-binlog.md) | Enable binary logging, configure the binlog job, REPLICATION SLAVE grant |
| [Restore: Logical backup (offsite)](docs/restore-logical.md) | Full procedure for restoring a mydumper backup from the offsite copy on `proxysql` |
| [Restore: Physical backup (offsite)](docs/restore-physical.md) | Full procedure for restoring an xtrabackup backup onto a separate server |
| [Point-in-time recovery](docs/pitr.md) | Replay binlogs after a physical restore to recover to a specific moment |
| [Verify a physical backup](docs/verify-backup.md) | Check `full-prepared` state and run `--prepare --export` sanity check |
| [Operations](docs/operations.md) | Upgrade the tool, run multiple configs, graceful stop |
| [Test record](docs/testing.md) | Complete end-to-end test run with real command output from `mysql-box` |

[↑ Back to top](#table-of-contents)

---

## 8. Configuration reference

### Global config

| Key | Default | Description |
|-----|---------|-------------|
| `backup_root` | *(required)* | Root directory for all backup data |
| `log_dir` | *(required)* | Directory for log files |
| `tmp_dir` | `/tmp/mysql-backup` | Temp directory (binlog state files, etc.) |
| `default_encryption` | `none` | `none` \| `xtrabackup_aes256` \| `gpg` |
| `default_retention_days` | `7` | Daily backup retention in days |
| `weekly_retention_weeks` | `4` | Weekly retention — keeps one backup per calendar week beyond the daily window. When a new backup is created in the same calendar week as an existing one, the older duplicate is removed (you will see `"Removing duplicate weekly backup"` in the log — this is normal). |
| `default_timeout_seconds` | `3600` | Per-job wall-clock timeout |
| `disk_space_factor` | `2.5` | Required free-space multiplier relative to estimated DB size |
| `debug` | `false` | Verbose debug logging |
| `graceful_stop_file` | *(none)* | Path to sentinel file — driver stops cleanly after current job if file exists |

### Instance config

| Key | Default | Description |
|-----|---------|-------------|
| `name` | *(required)* | Unique identifier |
| `host` | `localhost` | MySQL host |
| `port` | `3306` | MySQL port |
| `user` | `root` | MySQL user |
| `password` | *(none)* | Password literal (not recommended — use `~/.my.cnf` instead) |
| `password_env` | *(none)* | Env var holding the password (alternative to `~/.my.cnf`) |
| `socket` | *(none)* | Unix socket path (overrides host/port) |
| `pxc` | `false` | Mark as a PXC node |
| `pxc_desync` | `false` | Desync from cluster during backup |
| `pxc_cluster_name` | *(none)* | Cluster name (informational) |
| `replica_only` | `false` | Skip backup if instance is not an active replica |
| `read_only_only` | `false` | Skip backup if `read_only` / `super_read_only` is not ON |

### Physical backup options

| Key | Default | Description |
|-----|---------|-------------|
| `tool` | `xtrabackup` | `xtrabackup` or `mariadb-backup` |
| `xtrabackup_path` | *(from PATH)* | Full path to `xtrabackup` |
| `mariadb_backup_path` | *(from PATH)* | Full path to `mariadb-backup` |
| `backup_mode` | `full` | `full` or `incremental` |
| `full_backup_cycle` | *(none)* | Force a full at this interval when `backup_mode: incremental` — `daily`, `weekly`, or integer days |
| `prepare_after_backup` | `true` | Run decrypt + prepare after backup |
| `prepare_memory` | *(none)* | Memory for `--prepare` (e.g. `2G`) |
| `verify_after_backup` | `false` | Run `--prepare --export` as a post-backup sanity check |
| `defaults_file` | *(none)* | Path to a `[xtrabackup]` credentials file; when set, xtrabackup reads user/password/host/port from it instead of the command line |
| `save_replica_info` | `false` | Pass `--slave-info` to save replication coordinates |
| `kill_long_queries` | `false` | Kill long-running queries before backup |
| `kill_queries_timeout` | `10` | Kill queries running longer than N seconds |
| `kill_query_type` | `select` | `select` or `all` |
| `backup_copies` | `0` (unlimited) | Keep at most N local physical backup directories |
| `compression_algorithm` | *(none)* | e.g. `zstd` (requires xtrabackup ≥ 8.0.34) |
| `use_xtra_encryption` | `false` | Enable AES-256 encryption |
| `xtra_key_file` | *(none)* | Path to 32-byte binary key file (**recommended**) |
| `xtra_key_env` | `XTRABACKUP_ENCRYPTION_KEY` | Env var holding the key |
| `xtra_key` | *(none)* | Literal key in config (least secure) |
| `xtra_encrypt_algo` | `AES256` | Encryption algorithm |
| `gpg_recipient` | *(none)* | GPG recipient email — tars and GPG-encrypts the directory after backup |
| `extra_args` | `[]` | Extra args passed verbatim to xtrabackup |

### Logical backup options

| Key | Default | Description |
|-----|---------|-------------|
| `mydumper_path` | `/usr/bin/mydumper` | Full path to `mydumper` |
| `threads` | `4` | Parallel dump threads |
| `chunk_filesize` | `64` | Split table files at this size (MB) |
| `rows` | `50000` | Rows per chunk |
| `compress` | `true` | mydumper built-in compression |
| `dump_triggers` | `false` | Include stored triggers |
| `less_locking` | `false` | `--less-locking` mode |
| `use_numa` | `false` | `--use-numa` (requires `numactl`) |
| `ftwrl_guardian` | `false` | Abort if FTWRL lock takes too long |
| `incremental_since_days` | *(none)* | Only dump tables modified in last N days |
| `extra_args` | `[]` | Extra args passed verbatim to mydumper |

### Binlog backup options

| Key | Default | Description |
|-----|---------|-------------|
| `mysqlbinlog_path` | `/usr/bin/mysqlbinlog` | Full path to `mysqlbinlog` |
| `binlog_file` | *(none)* | Starting binlog filename — **required on first run only** |
| `binlog_log_prefix` | `mysql-bin` | Prefix used in error messages |
| `binlog_retention_days` | *(uses job/global)* | Independent retention for binlog backups |
| `insecure_connection` | `false` | `--ssl-mode=DISABLED` for trusted internal networks |
| `min_free_disk_pct` | `5.0` | Abort if free disk on backup volume drops below this % |
| `gpg_recipient` | *(none)* | GPG recipient email for per-file encryption |

### Storage target options

**S3:**

| Key | Description |
|-----|-------------|
| `bucket` | S3 bucket name |
| `prefix` | Key prefix (e.g. `prod/`) |
| `storage_class` | e.g. `STANDARD_IA`, `GLACIER` |
| `kms_key_id` | AWS KMS key ARN for server-side encryption |

**rsync:**

| Key | Description |
|-----|-------------|
| `target` | `user@host:/path` |
| `ssh_key` | Path to SSH private key |
| `options` | Extra rsync flags (e.g. `-z` for compression) |

**GCS:**

| Key | Description |
|-----|-------------|
| `bucket` | GCS bucket name |
| `prefix` | Object prefix |

**Azure Blob:**

| Key | Description |
|-----|-------------|
| `container` | Blob container name |
| `account_name` | Storage account name |
| `destination_path` | Blob path prefix |
| `sas_token` | SAS token (or set `AZURE_STORAGE_SAS_TOKEN` env var) |
| `connection_string` | Connection string (or set `AZURE_STORAGE_CONNECTION_STRING`) |

[↑ Back to top](#table-of-contents)

---

## 9. Troubleshooting

### Physical backup: `Access denied; you need BACKUP_ADMIN`

The backup user is missing the `BACKUP_ADMIN` privilege required by xtrabackup 8.x on MySQL 8:

```sql
GRANT BACKUP_ADMIN ON *.* TO 'backup'@'localhost';
FLUSH PRIVILEGES;
```

### Physical backup: `Invalid key length`

The xtrabackup key file was generated with `openssl rand -hex 32` (64 ASCII characters) instead of `openssl rand -out <file> 32` (32 binary bytes). Regenerate it:

```bash
openssl rand -out /root/.secrets/xtrabackup.key 32
chmod 600 /root/.secrets/xtrabackup.key
wc -c /root/.secrets/xtrabackup.key   # must print: 32
```

### Physical backup: `cannot open ./xtrabackup_info` during prepare

This happens if you try to run `xtrabackup --prepare` directly on an encrypted backup without decrypting first. The driver handles this automatically (decrypt then prepare). If running xtrabackup manually, decrypt first:

```bash
xtrabackup --decrypt=AES256 --encrypt-key-file=/root/.secrets/xtrabackup.key \
  --target-dir=/var/backups/mysql/instance/physical/20260322-010000

xtrabackup --prepare \
  --target-dir=/var/backups/mysql/instance/physical/20260322-010000
```

### Physical backup fails and leaves a partial directory

Remove the partial directory before retrying:

```bash
rm -rf /var/backups/mysql/<instance>/physical/<timestamp>
mysql_backup_driver --job physical-daily
```

### Binlog backup: `Access denied; you need REPLICATION SLAVE`

```sql
GRANT REPLICATION SLAVE ON *.* TO 'backup'@'localhost';
FLUSH PRIVILEGES;
```

### Binlog backup: wrong filename on first run

MySQL 8.0 defaults to the prefix `binlog` (e.g. `binlog.000001`), not `mysql-bin`. Check the actual filenames:

```sql
SHOW BINARY LOGS;
```

Set `binlog_file` to the oldest available file in the output.

### `Insufficient disk space` error

The backup volume does not have enough free space. The driver requires `2.5 × (estimated DB size) + 512 MB` free by default. Either:

- Free up space on the backup volume
- Reduce `disk_space_factor` in global config (not recommended below 1.5)
- Expand the backup volume

Check current usage:

```bash
df -h /var/backups/mysql
du -sh /var/lib/mysql
```

### `--run-scheduled` shows "No jobs selected" every minute

This is **expected behaviour** — not an error. The driver evaluates each job's `schedule_hint` on every cron invocation (every minute). At minutes where no job is due, it logs `[WARNING] No jobs selected` and exits cleanly. For example, with `binlog-5min` (`*/5 * * * *`), four out of every five minutes will show this message. The fifth minute will show the backup running.

To confirm your schedules are correct:
```bash
mysql_backup_driver --list-jobs          # shows all jobs
mysql_backup_driver --run-scheduled --dry-run  # shows which would run right now
```

### Physical backup: `This target seems to be already prepared`

Occurs when `xtrabackup --prepare` (without `--export`) is run manually on a backup that is already in `full-prepared` state. The backup is fine — do not run plain `--prepare` again. If you want to re-verify, use:
```bash
xtrabackup --prepare --export --target-dir=<backup-dir>
```

### Log files

```bash
# Driver log
tail -f /var/log/mysql-backup/mysql_backup.log

# Precheck log
tail -f /var/log/mysql-backup/mysql_backup_precheck.log

# Cron log (scheduled runs)
tail -f /var/log/mysql-backup/cron.log
```

### MySQL 8: `caching_sha2_password` auth errors

The Python `pymysql` client requires the `cryptography` package for `caching_sha2_password` (MySQL 8 default). It is included as a dependency. If you still see auth errors, verify it is installed in the venv:

```bash
source /root/mysql-backup-venv/bin/activate
python3 -c "import cryptography; print(cryptography.__version__)"
```

Or switch the backup user to `mysql_native_password`:

```sql
ALTER USER 'backup'@'localhost' IDENTIFIED WITH mysql_native_password BY 'your_password';
FLUSH PRIVILEGES;
```

[↑ Back to top](#table-of-contents)
