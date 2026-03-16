## mysql-backup

Python 3 backup suite for MySQL/MariaDB providing:

- Logical backups via `mydumper`
- Physical backups via `xtrabackup` / `mariadb-backup`
- Binlog backups via `mysqlbinlog`
- Encryption (xtrabackup AES256, optional GPG), deduplication, disk-space checks, PXC desync, and offsite copies (S3, rsync, GCS).

### Installation

Ensure Python 3.9+ and required system tools are installed: `mydumper`, `xtrabackup`/`mariadb-backup`, `mysqlbinlog`, `gpg`, `aws` (if using S3), `gsutil` (if using GCS), and `rsync`.

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

2. **Create the MySQL backup user**

On each MySQL server, create a dedicated backup user with the required privileges, for example:

```sql
CREATE USER IF NOT EXISTS 'backup'@'localhost' IDENTIFIED BY 'backup_pass';
GRANT RELOAD, LOCK TABLES, PROCESS, REPLICATION CLIENT, SELECT, SHOW VIEW ON *.* TO 'backup'@'localhost';
FLUSH PRIVILEGES;
```

3. **Export secrets as environment variables (as root)**

On the backup host, as root (or inside the root venv), set the environment variables referenced by your config (e.g. `password_env` and
encryption keys). For a simple setup:

```bash
export MYSQL_BACKUP_PASSWORD='backup_pass'
# export XTRABACKUP_ENCRYPTION_KEY='...'   # if using xtrabackup encryption
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

```bash
0 2 * * * /root/mysql-backup-venv/bin/mysql_backup_driver --job logical-daily >> /var/log/mysql-backup/cron.log 2>&1
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

