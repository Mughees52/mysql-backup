## mysql-backup

Python 3 backup suite for MySQL/MariaDB providing:

- Logical backups via `mydumper`
- Physical backups via `xtrabackup` / `mariadb-backup`
- Binlog backups via `mysqlbinlog`
- Encryption (xtrabackup AES256, optional GPG), deduplication, disk-space checks, PXC desync, and offsite copies (S3, rsync, GCS).

### Installation

Ensure Python 3.9+ and required system tools are installed: `mydumper`, `xtrabackup`/`mariadb-backup`, `mysqlbinlog`, `gpg`, `aws` (if using S3), `gsutil` (if using GCS), and `rsync`.

#### Option 1: Install directly from GitHub (pip)

On modern Ubuntu/Debian (PEP 668), you should install into a virtualenv instead of the system Python.

```bash
python3 -m venv ~/mysql-backup-venv
source ~/mysql-backup-venv/bin/activate

pip install --upgrade pip
pip install "git+https://github.com/Mughees52/mysql-backup.git"
```

This will install the `mysql_backup_driver` and `mysql_backup_precheck` CLIs into `~/mysql-backup-venv/bin/`.
Whenever you want to run backups:

```bash
source ~/mysql-backup-venv/bin/activate
mysql_backup_precheck
mysql_backup_driver --job logical-daily
```

#### Option 2: Install from source (local checkout + pip)

From the project root:

```bash
python3 -m venv venv
source venv/bin/activate

pip install --upgrade pip
pip install .
```

This will install the `mysql_backup_driver` and `mysql_backup_precheck` CLIs into `./venv/bin/`.

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

### Configuration

Copy one of the example configs and adjust it for your environment:

```bash
mkdir -p ~/.config/mysql-backup
cp etc/backup_config.yml ~/.config/mysql-backup/config.yml
# or start from the top-level config.yaml example in this repo
# cp config.yaml ~/.config/mysql-backup/config.yml
```

Edit `~/.config/mysql-backup/config.yml`:

- Define your MySQL instances under `instances`.
- Add jobs under `jobs` for `logical`, `physical`, and `binlog` backups.
- Configure storage targets under `storage` for S3/rsync/GCS.
- Optionally tune `global.default_timeout_seconds` and per-job backup options (encryption, dedup, etc.).

You can validate your configuration without running any backups:

```bash
backup_driver --validate-config
```

For a quick smoke test (validate + show number of jobs detected):

```bash
backup_driver --self-test
```

### Running backups

- List jobs:

```bash
backup_driver --list-jobs
```

- Run a specific job (e.g. logical daily backup):

```bash
backup_driver --job logical-daily
```

- Run only physical or binlog jobs by type:

```bash
backup_driver --type physical
backup_driver --type binlog
```

- See what would run without executing (dry run):

```bash
backup_driver --job physical-daily --dry-run
```

### Pre-checks (recommended before enabling cron)

Run comprehensive pre-checks against your config and target MySQL instances to ensure
all binaries, permissions, and directories are in place before scheduling backups:

- Pre-check all jobs:

```bash
backup_precheck
```

- Pre-check a single job:

```bash
backup_precheck --job logical-daily
```

- Pre-check all jobs for a specific instance:

```bash
backup_precheck --instance prod-mysql1
```

If any issue is found (missing binary, bad connectivity, missing env vars for encryption keys, etc.),
`backup_precheck` will exit non‑zero and list the problems.

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
0 2 * * * /usr/local/bin/mysql_backup_driver --job logical-daily >> /var/log/mysql-msp-backup/cron.log 2>&1
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
              args: ["backup_driver", "--config", "/etc/backup/config.yml", "--job", "logical-daily"]
              volumeMounts:
                - name: config
                  mountPath: /etc/backup
          restartPolicy: OnFailure
          volumes:
            - name: config
              configMap:
                name: mysql-msp-backup-config
```

