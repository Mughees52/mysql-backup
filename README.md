## mysql-backup

Python 3 backup suite for MySQL/MariaDB providing:

- Logical backups via `mydumper` (full and incremental, trigger dump, less-locking, FTWRL guardian)
- Physical backups via `xtrabackup` / `mariadb-backup` (full and incremental, configurable full-backup cycle)
- Binlog backups via `mysqlbinlog` (continuous streaming with position tracking)
- Encryption (xtrabackup AES256, optional GPG), deduplication, disk-space checks, PXC desync, and offsite copies (S3, rsync, GCS, Azure Blob Storage)
- Replica-aware safety gates (`replica_only`, `read_only_only`)
- Kill long-running queries before physical backup to avoid blocking
- Weekly retention tiers on top of daily retention
- Lock file to prevent concurrent driver instances
- Graceful stop support (clean shutdown between jobs via a sentinel file)

### Installation

Ensure Python 3.9+ and required system tools are installed: `mydumper`, `xtrabackup`/`mariadb-backup`, `mysqlbinlog`, `gpg`, `aws` (if using S3), `gsutil` (if using GCS), `az` (if using Azure Blob Storage), and `rsync`.

**Backup disk sizing:** The backup host disk should have at least **2.5× the size of the MySQL data directory** free. The driver enforces this by default via `disk_space_factor: 2.5` in the global config.

#### Option 1: Install directly from GitHub (pip, as root)

On the backup host we recommend running the driver as **root** (so it can write to backup and log directories
like `/var/backups/mysql` and `/var/log/mysql-backup`). On modern Ubuntu/Debian (PEP 668), install into a
virtualenv instead of the system Python:

```bash
sudo -i
python3 -m venv /root/mysql-backup-venv
source /root/mysql-backup-venv/bin/activate

pip install --upgrade pip
pip install "git+https://github.com/Mughees52/mysql-backup.git"
```

This will install the `mysql_backup_driver` and `mysql_backup_precheck` CLIs into `/root/mysql-backup-venv/bin/`.
Whenever you want to run backups on that host:

```bash
source /root/mysql-backup-venv/bin/activate
export MYSQL_BACKUP_PASSWORD='backup_pass'   # or your real secret
mysql_backup_precheck
mysql_backup_driver --job logical-daily
```

Important: **do not run `sudo -i` again after activating the virtualenv**, or you may lose the venv `PATH`
and see `command not found`. If that happens, either re-run `source /root/mysql-backup-venv/bin/activate`
or call the binaries with full paths:

```bash
/root/mysql-backup-venv/bin/mysql_backup_precheck
/root/mysql-backup-venv/bin/mysql_backup_driver --job logical-daily
```

To **upgrade an existing venv install** to the latest version from `main`:

```bash
source /root/mysql-backup-venv/bin/activate
pip install --force-reinstall --no-cache-dir "git+https://github.com/Mughees52/mysql-backup.git@main"
```

#### Option 2: Install from source (local checkout + pip, as root)

From the project root:

```bash
sudo -i
cd /opt/mysql-backup       # or wherever you cloned/downloaded the project
python3 -m venv venv
source venv/bin/activate

pip install --upgrade pip
pip install .
```

This will install the `mysql_backup_driver` and `mysql_backup_precheck` CLIs into `/opt/mysql-backup/venv/bin/`.

#### Option 3: Build and install RPM (RHEL/Alma/Rocky etc.)

From the project root, create a source tarball and build the RPM (on an RPM-based build host):

```bash
VERSION=0.1.0
tar czf mysql-backup-${VERSION}.tar.gz "Mughees scripts"  # or rename directory to mysql-backup-${VERSION}
mv mysql-backup-${VERSION}.tar.gz ~/rpmbuild/SOURCES/
cp mysql-backup.spec ~/rpmbuild/SPECS/

cd ~/rpmbuild/SPECS
rpmbuild -ba mysql-backup.spec
```

Then install on target servers:

```bash
sudo dnf install ~/rpmbuild/RPMS/noarch/mysql-backup-0.1.0-1*.rpm
```

This will place `backup_driver` and `backup_precheck` under `/usr/bin` and install the Python package
into the system site-packages. You can then configure `/etc` or per-user configs as described below.

### Configuration (step by step)

1. **Create the config directory and base config (as root)**

On each backup host, as root, create the config directory and copy or create a config:

```bash
sudo -i
mkdir -p /root/.config/mysql-backup
```

If you installed via **GitHub (pip)** you won't have `etc/backup_config.yml` on disk. Create the config file
manually (example below). If you cloned the repo or installed from source, you can copy the example config:

```bash
cp etc/backup_config.yml /root/.config/mysql-backup/config.yml
```

Then edit `/root/.config/mysql-backup/config.yml`:

- Define your MySQL instances under `instances`.
- Add jobs under `jobs` for `logical`, `physical`, and `binlog` backups.
- Configure storage targets under `storage` for S3/rsync/GCS.
- Optionally tune `global.default_timeout_seconds` and per-job backup options (encryption, dedup, etc.).

Minimal example config (logical backup):

```yaml
global:
  backup_root: /var/backups/mysql
  log_dir: /var/log/mysql-backup
  tmp_dir: /tmp/mysql-backup
  default_encryption: none
  default_retention_days: 3
  default_timeout_seconds: 1800

instances:
  - name: local-mysql
    host: 127.0.0.1
    port: 3306
    user: backup
    password_env: MYSQL_BACKUP_PASSWORD
    pxc: false

jobs:
  - name: logical-daily
    instance: local-mysql
    type: logical
    schedule_hint: "0 2 * * *"
    backup_options:
      mydumper_path: /usr/bin/mydumper
      threads: 2
      chunk_filesize: 64
      rows: 50000
      compress: true
    encryption: null
    dedup: false
    offsite_targets: []

storage: []
```

#### Global config reference

| Key | Default | Description |
|-----|---------|-------------|
| `backup_root` | *(required)* | Root directory where all backups are stored |
| `log_dir` | *(required)* | Directory for log files |
| `tmp_dir` | `/tmp/mysql-backup` | Temp directory (binlog state files, etc.) |
| `default_encryption` | `none` | `none` \| `xtrabackup_aes256` \| `gpg` |
| `default_retention_days` | `7` | How many days of daily backups to keep |
| `weekly_retention_weeks` | `4` | How many weeks of weekly backups to keep (one backup per calendar week beyond the daily window) |
| `default_timeout_seconds` | `3600` | Per-job wall-clock timeout |
| `disk_space_factor` | `2.5` | Required free space multiplier against the estimated DB size (2.5× recommended) |
| `debug` | `false` | Enable verbose debug logging |
| `graceful_stop_file` | *(none)* | Path to a sentinel file; if it exists, the driver finishes the current job then stops cleanly |

Full example config (logical + physical + binlog + offsite uploads) is available in the repo as `config.yaml`.
For convenience, here is the full content you can adapt:

```yaml
global:
  backup_root: /var/backups/mysql
  log_dir: /var/log/mysql-backup
  tmp_dir: /tmp/mysql-backup
  default_encryption: none          # none | xtrabackup_aes256 | gpg
  default_retention_days: 7
  weekly_retention_weeks: 4         # keep one backup/week for the last 4 weeks
  disk_space_factor: 2.5            # require 2.5x DB size free before backup
  debug: false
  graceful_stop_file: /root/.config/mysql-backup/GRACEFUL_STOP

instances:
  - name: prod-mysql1
    host: 10.0.0.10
    port: 3306
    user: backup
    password_env: MYSQL_BACKUP_PASSWORD
    pxc: false
    replica_only: true              # only run backups if this host is an active replica
    read_only_only: false           # set to true to additionally require read_only=ON

  - name: prod-pxc1
    host: 10.0.1.10
    port: 3306
    user: backup
    password_env: PXC_BACKUP_PASSWORD
    pxc: true
    pxc_desync: true
    pxc_cluster_name: prod-pxc
    replica_only: false

jobs:
  # Logical daily backup from standalone MySQL
  - name: logical-daily
    instance: prod-mysql1
    type: logical
    schedule_hint: "0 2 * * *"
    retention_days: 7               # override global retention per-job
    weekly_retention_weeks: 4
    backup_options:
      mydumper_path: /usr/bin/mydumper
      threads: 8
      chunk_filesize: 64
      rows: 500000
      compress: true
      dump_triggers: true           # dump stored triggers alongside tables
      less_locking: true            # reduce metadata lock hold time
      use_numa: false               # set to true if numactl is available
      ftwrl_guardian: false         # abort if FTWRL takes too long
      # incremental_since_days: 1   # only dump tables modified in last N days
      # extra_args: ["--long-query-guard=60", "--success-on-1146"]
    encryption: null
    dedup: false
    offsite_targets: ["s3-main", "rsync-dr"]

  # Physical full backup from standalone MySQL
  - name: physical-full-daily
    instance: prod-mysql1
    type: physical
    schedule_hint: "0 1 * * *"
    backup_options:
      tool: xtrabackup
      xtrabackup_path: /usr/bin/xtrabackup
      backup_mode: full             # full | incremental
      prepare_after_backup: true
      prepare_memory: 2G            # memory available to InnoDB during --prepare
      verify_after_backup: false    # run --prepare --export after backup as sanity check
      save_replica_info: true       # save replication coordinates (--slave-info)
      kill_long_queries: true       # kill blocking queries before backup starts
      kill_queries_timeout: 10      # kill queries running longer than N seconds
      kill_query_type: select       # select | all
      backup_copies: 2              # keep at most N local physical backups
      # compression_algorithm: zstd # use with xtrabackup 8.0.34+ for faster compression
      use_xtra_encryption: true
      # Choose ONE key source:
      xtra_key_file: /root/.secrets/xtrabackup.key   # recommended: 32-byte binary key
      # xtra_key_env: XTRABACKUP_ENCRYPTION_KEY       # env var alternative
      # xtra_key: "your-raw-key-here"                 # literal in config (least secure)
      xtra_encrypt_algo: AES256
      # gpg_recipient: "backup@example.com"
      # extra_args: ["--parallel=4"]
    encryption: xtrabackup_aes256
    dedup: true
    offsite_targets: ["s3-main", "rsync-dr"]

  # Physical full backup from PXC node (with desync)
  - name: pxc-physical-full-daily
    instance: prod-pxc1
    type: physical
    schedule_hint: "30 1 * * *"
    backup_options:
      tool: xtrabackup
      xtrabackup_path: /usr/bin/xtrabackup
      backup_mode: full
      prepare_after_backup: true
      prepare_memory: 2G
      save_replica_info: true
      kill_long_queries: true
      kill_queries_timeout: 10
      kill_query_type: select
      use_xtra_encryption: true
      xtra_key_file: /root/.secrets/pxc-xtrabackup.key
      xtra_encrypt_algo: AES256
    encryption: xtrabackup_aes256
    dedup: true
    offsite_targets: ["s3-main"]

  # Physical incremental backup (every 4h); runs a full when last full is >= 7 days old
  - name: physical-incremental-4h
    instance: prod-mysql1
    type: physical
    schedule_hint: "0 */4 * * *"
    backup_options:
      tool: xtrabackup
      xtrabackup_path: /usr/bin/xtrabackup
      backup_mode: incremental      # uses previous full as incremental base
      full_backup_cycle: weekly     # daily | weekly | 1–7 (days); force full at this interval
      prepare_after_backup: false
      save_replica_info: true
      use_xtra_encryption: true
      xtra_key_file: /root/.secrets/xtrabackup.key
      xtra_encrypt_algo: AES256
    encryption: xtrabackup_aes256
    dedup: true
    offsite_targets: ["s3-main"]

  # Binlog backup every 5 minutes
  - name: binlog-continuous
    instance: prod-mysql1
    type: binlog
    schedule_hint: "*/5 * * * *"
    backup_options:
      mysqlbinlog_path: /usr/bin/mysqlbinlog
      binlog_file: mysql-bin.000001 # only required on the very first run
      binlog_log_prefix: mysql-bin  # prefix used in error messages to guide bootstrap
      binlog_retention_days: 30     # separate retention for binlogs (overrides global)
      insecure_connection: false    # set true to disable TLS on trusted internal networks
      min_free_disk_pct: 5.0        # abort binlog backup if free disk drops below this %
      # gpg_recipient: "backup@example.com"
    encryption: null
    dedup: false
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
      # ssh_user: backup
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
      # sas_token: "sp=rw&st=..."        # or set AZURE_STORAGE_SAS_TOKEN env var
      # connection_string: "DefaultEndpoints..."  # or set AZURE_STORAGE_CONNECTION_STRING
```

#### Instance config reference

| Key | Default | Description |
|-----|---------|-------------|
| `name` | *(required)* | Unique identifier for the instance |
| `host` | `localhost` | MySQL host |
| `port` | `3306` | MySQL port |
| `user` | `root` | MySQL user |
| `password_env` | *(none)* | Environment variable holding the password (recommended over `password`) |
| `socket` | *(none)* | Unix socket path (overrides host/port) |
| `pxc` | `false` | Mark as Percona XtraDB Cluster node |
| `pxc_desync` | `false` | Desync node from cluster during backup |
| `pxc_cluster_name` | *(none)* | Cluster name (informational) |
| `replica_only` | `false` | Skip backup if the instance is not currently running as an active replica |
| `read_only_only` | `false` | Skip backup if `read_only` or `super_read_only` is not enabled |

#### Physical backup `backup_options` reference

| Key | Default | Description |
|-----|---------|-------------|
| `tool` | `xtrabackup` | `xtrabackup` or `mariadb-backup` |
| `xtrabackup_path` | *(from PATH)* | Full path to `xtrabackup` binary |
| `mariadb_backup_path` | *(from PATH)* | Full path to `mariadb-backup` binary |
| `backup_mode` | `full` | `full` or `incremental` |
| `full_backup_cycle` | *(none)* | `daily`, `weekly`, or an integer day count — force a full backup at this interval when `backup_mode: incremental` |
| `prepare_after_backup` | `true` | Run `--prepare` immediately after backup |
| `prepare_memory` | *(none)* | Memory to use during `--prepare` (e.g. `2G`) |
| `verify_after_backup` | `false` | Run `--prepare --export` as a post-backup verification step |
| `save_replica_info` | `false` | Add `--slave-info` to save replication coordinates |
| `kill_long_queries` | `false` | Kill long-running queries before backup starts |
| `kill_queries_timeout` | `10` | Kill queries running longer than N seconds |
| `kill_query_type` | `select` | `select` (only SELECTs) or `all` (any non-replication query) |
| `backup_copies` | `0` (unlimited) | Keep at most N local physical backup directories |
| `compression_algorithm` | *(none)* | Compression algorithm passed as `--compress-algorithm` (e.g. `zstd` for xtrabackup ≥ 8.0.34) |
| `use_xtra_encryption` | `false` | Enable built-in AES-256 encryption |
| `xtra_key_file` | *(none)* | Path to 32-byte binary key file (recommended) |
| `xtra_key_env` | `XTRABACKUP_ENCRYPTION_KEY` | Env var holding the encryption key |
| `xtra_key` | *(none)* | Literal key string in config (least secure) |
| `xtra_encrypt_algo` | `AES256` | Encryption algorithm |
| `gpg_recipient` | *(none)* | GPG recipient email; if set, directory is tarred and GPG-encrypted after backup |
| `extra_args` | `[]` | Additional arguments passed verbatim to `xtrabackup` |

#### Logical backup `backup_options` reference

| Key | Default | Description |
|-----|---------|-------------|
| `mydumper_path` | `/usr/bin/mydumper` | Full path to `mydumper` binary |
| `threads` | `4` | Number of parallel dump threads |
| `chunk_filesize` | `64` | Split table files at this size (MB) |
| `rows` | `50000` | Rows per chunk |
| `compress` | `true` | Compress output with mydumper's built-in compression |
| `dump_triggers` | `false` | Include stored triggers in the dump |
| `less_locking` | `false` | Use `--less-locking` to reduce metadata lock hold time |
| `use_numa` | `false` | Pass `--use-numa` (requires `numactl`) |
| `ftwrl_guardian` | `false` | Pass `--use-ftwrl-guardian` to abort if the global lock takes too long |
| `incremental_since_days` | *(none)* | Only dump tables modified in the last N days (`--updated-since`) |
| `extra_args` | `[]` | Additional arguments passed verbatim to `mydumper` |

#### Binlog backup `backup_options` reference

| Key | Default | Description |
|-----|---------|-------------|
| `mysqlbinlog_path` | `/usr/bin/mysqlbinlog` | Full path to `mysqlbinlog` binary |
| `binlog_file` | *(none)* | Starting binlog file name — **required on the first run** |
| `binlog_log_prefix` | `mysql-bin` | Binlog filename prefix (used in error messages) |
| `binlog_retention_days` | *(uses job/global)* | Retention for binlog backups, independent of other job types |
| `insecure_connection` | `false` | Disable TLS (`--ssl-mode=DISABLED`) for internal trusted networks |
| `min_free_disk_pct` | `5.0` | Abort if free disk on the backup volume drops below this percentage |
| `gpg_recipient` | *(none)* | GPG recipient email for per-file encryption |

#### Bootstrapping the binlog puller on a new host

On first setup (or after a long gap), the oldest binlog file on the primary may no longer exist. Check which file is the oldest available:

```bash
mysql -h <primary_ip> -u backup -p -e "SHOW BINARY LOGS;" | head -n 2
# Example output:
# Log_name          File_size
# mysql-bin.004884  123456
```

Set `binlog_file: mysql-bin.004884` (the oldest available file) in the job's `backup_options` and run the job once. On subsequent runs the driver reads its stored position state and the `binlog_file` setting is ignored.

2. **Create the MySQL backup user**

On each MySQL server, create a dedicated backup user with the required privileges.
For MySQL 8+ with `xtrabackup`, you **must** grant `BACKUP_ADMIN` as well:

```sql
CREATE USER IF NOT EXISTS 'backup'@'localhost' IDENTIFIED BY 'backup_pass';
GRANT BACKUP_ADMIN, RELOAD, LOCK TABLES, PROCESS, REPLICATION CLIENT, SELECT, SHOW VIEW
  ON *.* TO 'backup'@'localhost';
FLUSH PRIVILEGES;
```

3. **Provide MySQL credentials and (optionally) encryption keys**

On the backup host, as root (or inside the root venv), set the environment variables referenced by your config (e.g. `password_env` and
encryption keys). For a simple setup:

```bash
export MYSQL_BACKUP_PASSWORD='backup_pass'
# export XTRABACKUP_ENCRYPTION_KEY='...'   # if using xtrabackup encryption via xtra_key_env
```

Alternatively (recommended), configure xtrabackup encryption with a **root-readable key file** in your job's `backup_options`:

```yaml
backup_options:
  use_xtra_encryption: true
  xtra_key_file: /root/.secrets/xtrabackup.key
  xtra_encrypt_algo: AES256
```

#### GPG key management

Generate a new GPG key pair for backup encryption (run as root on the backup host):

```bash
gpg --full-generate-key
# Choose: (1) RSA and RSA, keysize 4096, no expiry
# Real name: Backup Encryption
# Email address: backup.encryption@example.com
```

Export both keys for safe storage (e.g. in a secrets vault):

```bash
gpg --armor --export backup.encryption@example.com > public.key
gpg --armor --export-secret-key backup.encryption@example.com > private.key
# Save the passphrase used during key creation alongside these files
```

To **import an existing key pair** on a new backup host:

```bash
gpg --import private.key   # prompts for passphrase
gpg --import public.key
gpg --list-keys            # verify import
```

To create a suitable AES-256 key file for xtrabackup (expects **raw 32‑byte key**):

```bash
umask 077
openssl rand -out /root/.secrets/xtrabackup.key 32
chmod 600 /root/.secrets/xtrabackup.key
wc -c /root/.secrets/xtrabackup.key   # should print: 32 /root/.secrets/xtrabackup.key
```

Or (less secure) set the literal key in config:

```yaml
backup_options:
  use_xtra_encryption: true
  xtra_key: "your-key-material-here"
  xtra_encrypt_algo: AES256
```

4. **Validate configuration (optional but recommended)**

You can validate your configuration without running any backups:

```bash
mysql_backup_driver --validate-config
```

For a quick smoke test (validate + show number of jobs detected):

```bash
mysql_backup_driver --self-test
```

### Running backups

1. **Run pre-checks (recommended before enabling cron)**

Run comprehensive pre-checks against your config and target MySQL instances to ensure
all binaries, permissions, and directories are in place before scheduling backups:

- Pre-check all jobs:

```bash
mysql_backup_precheck
```

- Pre-check a single job:

```bash
mysql_backup_precheck --job logical-daily
```

- Pre-check all jobs for a specific instance:

```bash
mysql_backup_precheck --instance prod-mysql1
```

If any issue is found (missing binary, bad connectivity, missing env vars for encryption keys, etc.),
`mysql_backup_precheck` will exit non‑zero and list the problems.

If physical backups fail with errors like:

- `Access denied; you need (at least one of) the BACKUP_ADMIN privilege(s) for this operation`
- `encryption: unable to set libgcrypt cipher key - ... Invalid key length`
- `Can't create/write to file '.../xtrabackup_logfile.xbcrypt' (OS errno 17 - File exists)`

then:

- Ensure the backup user has `BACKUP_ADMIN` as shown above.
- Ensure the xtrabackup key file is a **32‑byte binary file** (see the `openssl rand -out ... 32` example).
- Remove any partial backup directory and rerun, e.g.:

  ```bash
  rm -rf /var/backups/mysql/<instance>/physical/<timestamp-dir>
  ```

2. **List and run jobs**

- List jobs:

```bash
mysql_backup_driver --list-jobs
```

- Run a specific job (e.g. logical daily backup):

```bash
mysql_backup_driver --job logical-daily
```

- Run only physical or binlog jobs by type:

```bash
mysql_backup_driver --type physical
mysql_backup_driver --type binlog
```

- See what would run without executing (dry run):

```bash
mysql_backup_driver --job physical-daily --dry-run
```

### Running multiple configs on the same host

If you manage several MySQL instances from a single backup server, run each config with its own lock file:

```bash
mysql_backup_driver --config /root/.config/mysql-backup/prod-mysql1.yml --lock-file /tmp/backup-prod-mysql1.lock
mysql_backup_driver --config /root/.config/mysql-backup/prod-mysql2.yml --lock-file /tmp/backup-prod-mysql2.lock
```

Without `--lock-file` the driver defaults to `/tmp/mysql-backup-driver.lock`, so all jobs share one lock and cannot run concurrently.

### Graceful stop

To stop the driver cleanly between jobs (e.g. after a config change) without killing a running backup, create the file specified in `graceful_stop_file`:

```bash
# Create the sentinel file
touch /root/.config/mysql-backup/GRACEFUL_STOP

# The driver will finish its current job and then exit.
# Remove the file after restart so it does not block the next cron run.
sleep 60 && rm /root/.config/mysql-backup/GRACEFUL_STOP
```

You can combine both into one line:

```bash
(touch /root/.config/mysql-backup/GRACEFUL_STOP && sleep 60 && rm /root/.config/mysql-backup/GRACEFUL_STOP) &
```

### Monitoring a running backup

Check whether the driver is running and see all child processes (xtrabackup, mydumper, mysqlbinlog, etc.):

```bash
pgrep -f mysql_backup_driver | xargs ps -opgrp --no-headers \
  | sort | uniq \
  | while read -r grp; do
      pgrep -g "$grp" | xargs ps -o pid,ppid,user,stime,etime,cmd -f
    done
```

Check the log files:

```bash
cd /var/log/mysql-backup
tail -f backup_driver.log
```

### Notes for MySQL 8 (auth and permissions)

- For MySQL 8 default `caching_sha2_password` auth, the Python client requires the `cryptography` package
  (already included as a dependency). Ensure it is installed, or switch the backup user to `mysql_native_password`
  if you prefer.
- Logical backups with `mydumper` may require `SHOW VIEW` on schemas such as `sys`. Either:
  - Grant `SHOW VIEW` to the backup user, e.g.:
    ```sql
    GRANT SHOW VIEW ON *.* TO 'backup'@'localhost';
    FLUSH PRIVILEGES;
    ```
  - Or configure `mydumper` to exclude schemas like `sys` via `backup_options.extra_args`.

### Cron example (VM/bare metal)

Single instance:

```bash
0 2 * * * /root/mysql-backup-venv/bin/mysql_backup_driver --job logical-daily >> /var/log/mysql-backup/cron.log 2>&1
```

Multiple configs on the same host (use separate lock files):

```bash
0 1 * * * root /root/mysql-backup-venv/bin/mysql_backup_driver \
  --config /root/.config/mysql-backup/prod-mysql1.yml \
  --lock-file /tmp/backup-prod1.lock >> /var/log/mysql-backup/prod1-cron.log 2>&1

0 1 * * * root /root/mysql-backup-venv/bin/mysql_backup_driver \
  --config /root/.config/mysql-backup/prod-mysql2.yml \
  --lock-file /tmp/backup-prod2.lock >> /var/log/mysql-backup/prod2-cron.log 2>&1
```

### Kubernetes CronJob example

```yaml
apiVersion: batch/v1
kind: CronJob
metadata:
  name: mysql-backup-logical-daily
spec:
  schedule: "0 2 * * *"
  jobTemplate:
    spec:
      template:
        spec:
          containers:
            - name: backup
              image: your-registry/mysql-backup:latest
              args: ["mysql_backup_driver", "--config", "/etc/backup/config.yml", "--job", "logical-daily"]
              volumeMounts:
                - name: config
                  mountPath: /etc/backup
          restartPolicy: OnFailure
          volumes:
            - name: config
              configMap:
                name: mysql-backup-config
```

