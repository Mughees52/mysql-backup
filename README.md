# mysql-backup

Python 3 backup suite for MySQL/MariaDB providing:

- **Logical backups** via `mydumper` — full and incremental, trigger dump, less-locking, FTWRL guardian
- **Physical backups** via `xtrabackup` / `mariadb-backup` — full and incremental, configurable full-backup cycle, AES-256 encryption
- **Binlog backups** via `mysqlbinlog` — continuous remote streaming with position tracking
- **Offsite storage** — S3, rsync, GCS, Azure Blob Storage
- **Retention** — daily + weekly tiers (GASCAN-compliant)
- **Safety gates** — replica-aware (`replica_only`, `read_only_only`), disk-space checks (2.5× by default)
- **PXC support** — automatic desync/resync around physical backups on Percona XtraDB Cluster nodes
- **Operational** — lock file (prevents concurrent runs), graceful stop, kill long-running queries before backup

---

## Table of Contents

1. [Prerequisites](#1-prerequisites)
2. [Installation](#2-installation)
3. [MySQL user setup](#3-mysql-user-setup)
4. [Configuration](#4-configuration)
5. [Running backups](#5-running-backups)
6. [Scheduling with cron](#6-scheduling-with-cron)
7. [How-to guides](#7-how-to-guides)
   - [How to set up physical backups with AES-256 encryption](#how-to-set-up-physical-backups-with-aes-256-encryption)
   - [How to set up binlog backups](#how-to-set-up-binlog-backups)
   - [How to restore a logical backup](#how-to-restore-a-logical-backup)
   - [How to restore a physical backup](#how-to-restore-a-physical-backup)
   - [How to do point-in-time recovery using binlogs](#how-to-do-point-in-time-recovery-using-binlogs)
   - [How to verify a physical backup without restoring](#how-to-verify-a-physical-backup-without-restoring)
   - [How to upgrade the tool](#how-to-upgrade-the-tool)
   - [How to run multiple configs on the same host](#how-to-run-multiple-configs-on-the-same-host)
   - [How to use graceful stop](#how-to-use-graceful-stop)
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
    user: backup
    password_env: MYSQL_BACKUP_PASSWORD   # export this before running

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

Set the password before running (replace `s3cr3t` with the actual password you set when creating the backup user):

```bash
export MYSQL_BACKUP_PASSWORD='s3cr3t'
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
    user: backup
    password_env: MYSQL_BACKUP_PASSWORD

  - name: prod-replica1
    host: localhost
    port: 3306
    user: backup
    password_env: MYSQL_BACKUP_PASSWORD
    replica_only: true          # skip backup if not currently an active replica
    read_only_only: false

  - name: prod-pxc1
    host: 10.0.1.10
    port: 3306
    user: backup
    password_env: PXC_BACKUP_PASSWORD
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
MYSQL_BACKUP_PASSWORD=s3cr3t

* * * * * root /root/mysql-backup-venv/bin/mysql_backup_driver --run-scheduled >> /var/log/mysql-backup/cron.log 2>&1
```

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

---

## 7. How-to guides

### How to set up physical backups with AES-256 encryption

Physical backups can be encrypted with xtrabackup's built-in AES-256. The encrypted backup is useless without the key, so store it securely.

**Step 1 — Generate the key**

xtrabackup expects a **raw 32-byte binary key** file (not hex-encoded):

```bash
mkdir -p /root/.secrets
openssl rand -out /root/.secrets/xtrabackup.key 32
chmod 600 /root/.secrets/xtrabackup.key
# Verify: should print "32 /root/.secrets/xtrabackup.key"
wc -c /root/.secrets/xtrabackup.key
```

> **Common mistake:** `openssl rand -hex 32` generates a 64-character hex string, not a 32-byte binary key. xtrabackup will reject it with `Invalid key length`. Always use `openssl rand -out <file> 32`.

**Step 2 — Create an xtrabackup credentials file**

Instead of passing `--password=` on the command line (which appears in `ps` output), store xtrabackup credentials in a dedicated config file:

```bash
cat > /root/.config/mysql-backup/xtrabackup.cnf << 'EOF'
[xtrabackup]
user=backup
password=s3cr3t
host=localhost
port=3306
EOF
chmod 600 /root/.config/mysql-backup/xtrabackup.cnf
```

**Step 3 — Reference both in your job config**

```yaml
jobs:
  - name: physical-daily
    instance: prod-mysql1
    type: physical
    backup_options:
      tool: xtrabackup
      xtrabackup_path: /usr/bin/xtrabackup
      backup_mode: full
      prepare_after_backup: true
      defaults_file: /root/.config/mysql-backup/xtrabackup.cnf
      use_xtra_encryption: true
      xtra_key_file: /root/.secrets/xtrabackup.key
      xtra_encrypt_algo: AES256
    encryption: xtrabackup_aes256
```

**How prepare works with encryption**

When `prepare_after_backup: true`, the driver automatically:
1. Runs `xtrabackup --decrypt=AES256 --encrypt-key-file=... --target-dir=...` to decrypt the `.xbcrypt` files in-place
2. Runs `xtrabackup --prepare --target-dir=...` to apply the redo log and make the backup consistent

This is a two-step process because xtrabackup cannot apply the redo log to encrypted files directly. Both steps happen automatically — you do not need to do anything manually.

---

### How to set up binlog backups

Binlog backups stream binlog events from MySQL using `mysqlbinlog --read-from-remote-server`. They provide point-in-time recovery capability between physical/logical backups.

**Step 1 — Check binary logging is enabled**

```sql
SHOW VARIABLES LIKE 'log_bin';
-- Value should be ON
```

If it is OFF, enable it in `/etc/mysql/mysql.conf.d/mysqld.cnf`:

```ini
[mysqld]
log_bin = binlog
server-id = 1
```

Then restart MySQL.

**Step 2 — Check the current binlog filename**

MySQL 8.0 uses `binlog.XXXXXX` as the default prefix (not `mysql-bin`). Check what your server uses:

```sql
SHOW BINARY LOGS;
-- Example output:
-- binlog.000033  201
-- binlog.000034  201
-- binlog.000068  157   ← this is the current file
```

Use the **oldest available** file as the starting point for your first run.

**Step 3 — Configure the job**

```yaml
jobs:
  - name: binlog-5min
    instance: prod-mysql1
    type: binlog
    schedule_hint: "*/5 * * * *"
    backup_options:
      mysqlbinlog_path: /usr/bin/mysqlbinlog
      binlog_file: binlog.000033    # oldest available file — first run only
      binlog_retention_days: 30
      min_free_disk_pct: 5.0
```

After the first successful run, the driver saves its position to `/tmp/mysql-backup/binlog_state_<jobname>.txt` and uses that on every subsequent run. The `binlog_file` config setting is ignored once state exists.

**Step 4 — Ensure the backup user has REPLICATION SLAVE**

```sql
GRANT REPLICATION SLAVE ON *.* TO 'backup'@'localhost';
FLUSH PRIVILEGES;
```

This is required for `mysqlbinlog --read-from-remote-server`. Without it you will get:
```
ERROR: Got error reading packet from server: Access denied; you need (at least one of) the REPLICATION SLAVE privilege(s)
```

---

### How to restore a logical backup

Logical backups are created by `mydumper` and restored with `myloader`.

**Install myloader** (same package as mydumper):

```bash
# Ubuntu/Debian
apt-get install mydumper

# RHEL/Alma/Rocky
dnf install mydumper
```

**Restore a full backup to the same or a different server:**

```bash
# Find the backup directory
ls /var/backups/mysql/<instance>/logical/
# e.g. 20260322-020000

# Restore all databases (will overwrite existing data)
myloader \
  --host=localhost \
  --port=3306 \
  --user=root \
  --password=root_password \
  --directory=/var/backups/mysql/prod-mysql1/logical/20260322-020000 \
  --overwrite-tables \
  --threads=4 \
  --verbose=3
```

**Restore a single database:**

```bash
myloader \
  --host=localhost \
  --port=3306 \
  --user=root \
  --password=root_password \
  --directory=/var/backups/mysql/prod-mysql1/logical/20260322-020000 \
  --source-db=myapp \
  --database=myapp_restored \
  --overwrite-tables \
  --threads=4
```

> The backup directory stores one file per table chunk — restoring a single table is possible by copying only the relevant files into a new directory and pointing `myloader` at it.

---

### How to restore a physical backup

Physical backups taken with xtrabackup can be restored by copying the data directory back to MySQL.

> **Prerequisites:** MySQL must be stopped before restoring. The data directory must be empty (or you must be willing to overwrite it).

**Step 1 — Locate the prepared backup**

After a successful job, the backup directory contains a fully prepared (apply-log completed) copy of the MySQL data directory:

```bash
ls /var/backups/mysql/<instance>/physical/
# e.g. 20260322-010000
```

If the backup was encrypted, the driver has already decrypted and prepared it automatically (decrypt → prepare happens immediately after backup). The directory is ready to restore.

**Step 2 — Stop MySQL**

```bash
systemctl stop mysql
```

**Step 3 — Move or clear the data directory**

```bash
# Option A: move existing data dir out of the way
mv /var/lib/mysql /var/lib/mysql.bak

# Option B: wipe it (destructive — no recovery possible)
rm -rf /var/lib/mysql/*
```

**Step 4 — Copy the backup into place**

```bash
xtrabackup \
  --copy-back \
  --target-dir=/var/backups/mysql/prod-mysql1/physical/20260322-010000 \
  --datadir=/var/lib/mysql
```

**Step 5 — Fix ownership and start MySQL**

```bash
chown -R mysql:mysql /var/lib/mysql
systemctl start mysql
```

**Step 6 — Verify**

```bash
mysql -u root -p -e "SHOW DATABASES;"
```

---

### How to do point-in-time recovery using binlogs

Use this when you need to recover to a specific time (e.g. just before a bad `DROP TABLE`), combining a physical (or logical) backup with binlog backups.

**Step 1 — Restore the most recent physical backup** before the incident (see [How to restore a physical backup](#how-to-restore-a-physical-backup)).

**Step 2 — Find the binlog position in the backup**

After `--copy-back`, the backup contains `xtrabackup_binlog_info` with the exact binlog position the backup was taken at:

```bash
cat /var/backups/mysql/prod-mysql1/physical/20260322-010000/xtrabackup_binlog_info
# binlog.000065   157
```

**Step 3 — Replay binlogs up to the incident**

Collect all binlog backup files from that position up to just before the incident. Binlog backups are stored in:

```
/var/backups/mysql/<instance>/binlog/<timestamp>/binlog.sql
```

Find the right time range:

```bash
ls -lt /var/backups/mysql/prod-mysql1/binlog/
```

Use `mysqlbinlog` to replay, stopping just before the destructive event:

```bash
# Replay from backup position to a specific datetime (exclude the DROP TABLE at 14:32:00)
mysqlbinlog \
  --start-position=157 \
  --stop-datetime="2026-03-22 14:31:59" \
  /var/backups/mysql/prod-mysql1/binlog/20260322-140000/binlog.sql \
  /var/backups/mysql/prod-mysql1/binlog/20260322-143000/binlog.sql \
  | mysql -u root -p
```

Or stop at a specific binlog position (more precise):

```bash
mysqlbinlog \
  --start-position=157 \
  --stop-position=98765 \
  /var/backups/mysql/prod-mysql1/binlog/20260322-140000/binlog.sql \
  | mysql -u root -p
```

---

### How to verify a physical backup without restoring

Use xtrabackup's `--prepare --export` to perform a read-only sanity check on a backup without actually restoring it. This confirms the InnoDB pages are consistent.

```bash
BACKUP_DIR=/var/backups/mysql/prod-mysql1/physical/20260322-010000

xtrabackup --prepare --export --target-dir="$BACKUP_DIR"
# Should end with: "completed OK!"
```

You can also enable this check automatically after every backup by setting `verify_after_backup: true` in the job's `backup_options`.

---

### How to upgrade the tool

```bash
sudo -i
source /root/mysql-backup-venv/bin/activate

# Upgrade to latest main branch
pip install --force-reinstall --no-cache-dir "git+https://github.com/Mughees52/mysql-backup.git@main"

# Verify the new version is active
mysql_backup_driver --validate-config
```

To upgrade from a local source checkout:

```bash
source /root/mysql-backup-venv/bin/activate
pip install --force-reinstall /path/to/mysql-backup-source/
```

---

### How to run multiple configs on the same host

If one backup host manages several MySQL instances, use a separate config file and lock file for each:

```bash
mysql_backup_driver \
  --config /root/.config/mysql-backup/prod-mysql1.yml \
  --lock-file /tmp/backup-prod-mysql1.lock

mysql_backup_driver \
  --config /root/.config/mysql-backup/prod-mysql2.yml \
  --lock-file /tmp/backup-prod-mysql2.lock
```

Without `--lock-file` all invocations share the default `/tmp/mysql-backup-driver.lock` and cannot run concurrently.

---

### How to use graceful stop

To stop the driver cleanly between jobs (e.g. before a maintenance window) without killing a running backup, create the sentinel file configured in `graceful_stop_file`:

```bash
# Tell the driver to stop after its current job finishes
touch /root/.config/mysql-backup/GRACEFUL_STOP

# The driver exits cleanly. Remove the file before the next scheduled run.
rm /root/.config/mysql-backup/GRACEFUL_STOP
```

To auto-remove after 5 minutes:

```bash
touch /root/.config/mysql-backup/GRACEFUL_STOP
(sleep 300 && rm -f /root/.config/mysql-backup/GRACEFUL_STOP) &
```

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
| `weekly_retention_weeks` | `4` | Weekly retention (one backup per calendar week, beyond the daily window) |
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
| `password_env` | *(none)* | Env var holding the password (recommended) |
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

### Log files

```bash
# Driver log
tail -f /var/log/mysql-backup/mysql_backup.log

# Precheck log
tail -f /var/log/mysql-backup/mysql_backup_precheck.log
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
