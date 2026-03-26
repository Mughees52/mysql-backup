# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**mysql-backup** is a Python 3 backup suite for MySQL/MariaDB. It orchestrates logical (mydumper), physical (xtrabackup/mariadb-backup), and binlog (mysqlbinlog) backups with encryption, deduplication, offsite storage, and configurable daily/weekly retention policies.

The single package namespace is `mysql_backup/`.

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

The decrypt step writes plain files alongside their `.xbcrypt` originals — both exist on disk simultaneously after the pipeline runs. `xtrabackup --copy-back` skips `.xbcrypt` files automatically and copies only the plain decrypted files to the data directory. This means no separate decrypt step is needed at restore time when `prepare_after_backup: true`.

### xtrabackup credentials file (`defaults_file`)
When `defaults_file` is set in a physical job's `backup_options`, the path is passed as `--defaults-file=<path>` (must be the first argument after the binary) and `--password` is omitted from the command line. The file uses `[xtrabackup]` section format:
```ini
[xtrabackup]
user=backup
password=...
host=localhost
port=3306
```

### MySQL credential resolution order
`mysql_client.py` resolves credentials in this order:
1. `password` literal in config → used directly
2. `password_env` in config → reads the named env var
3. Neither set → `get_connection()` passes `read_default_file=~/.my.cnf` to pymysql; user/password come from the `[client]` section of that file

The live deployment on `mysql-box` uses option 3: no `password_env` in `config.yml`, credentials stored in `/root/.my.cnf`. xtrabackup physical backups use a separate `defaults_file` (`/root/.config/mysql-backup/xtrabackup.cnf`) that is unchanged.

### Binlog streaming
`backup_binlog.py` always passes `--read-from-remote-server` to mysqlbinlog. Without it, mysqlbinlog tries to read a local file. The backup user needs `REPLICATION SLAVE` privilege. MySQL 8.0 uses `binlog.XXXXXX` filenames (not `mysql-bin.XXXXXX`).

### MySQL user required grants
```sql
GRANT SELECT, RELOAD, PROCESS, LOCK TABLES, REPLICATION CLIENT, SHOW VIEW, BACKUP_ADMIN,
      REPLICATION SLAVE ON *.* TO 'backup'@'localhost';
```

### rsync offsite — directory vs contents
`_push_rsync` in `storage_remote.py` passes `local_path` **without** a trailing slash to rsync. This transfers the backup directory as a named subdirectory inside the destination (e.g., `20260325-142006/` appears under the target root). A trailing slash would strip the directory name and dump contents flat — each backup would overwrite the previous. The live rsync target is `ubuntu@192.168.2.3:/var/backups/mysql-offsite` on `proxysql`, accessed via SSH key at `/root/.ssh/id_ed25519`.

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

## Documentation

All guides and runbooks live in `docs/`:

| File | Contents |
|------|----------|
| `docs/testing.md` | Complete end-to-end test record with real captured output from `mysql-box`. 15 tests: config validation, self-test, list-jobs, precheck, dry-run, logical backup, physical backup (encrypt+decrypt+prepare), binlog backup, retention, lock file, graceful stop, `--run-scheduled` dispatch, rsync offsite upload, logical restore from offsite. |
| `docs/restore-physical.md` | Full restore procedure for a physical xtrabackup backup onto a separate server. Validated 2026-03-25 on `proxysql` (Ubuntu 22.04, MySQL 8.0.45). Transfer goes mysql-box → Mac host → proxysql (two `multipass transfer` hops). xtrabackup 8.0.35-35 installed via the xtrabackup apt package. |
| `docs/restore-logical.md` | Restore procedure for the offsite mydumper backup from `proxysql:/var/backups/mysql-offsite/` using `myloader`. Validated 2026-03-25. myloader 0.10.0 silently skips databases with no table data — apply `<db>-schema-create.sql.gz` manually. Root uses `auth_socket`: use `--socket`, not `--password`. |
| `docs/setup-encryption.md` | AES-256 encryption setup: key generation, credentials file, job config. |
| `docs/setup-binlog.md` | Binlog backup setup: enable binary logging, job config, REPLICATION SLAVE grant. |
| `docs/pitr.md` | Point-in-time recovery using binlogs after a physical restore. |
| `docs/verify-backup.md` | Verify a physical backup without restoring: `full-prepared` check and `--prepare --export`. |
| `docs/operations.md` | Upgrade, multiple configs, graceful stop. |
| `docs/conversationlog.md` | Chronological log of all development sessions: what was built, bugs fixed, decisions made, and current system state. Read this first in any new session to understand the project history. |

## Documentation Rule

**No task is complete until all relevant docs are updated.** Apply the matrix below after every change:

| Change type | README.md | CLAUDE.md | docs/testing.md | docs/restore-physical.md | docs/restore-logical.md |
|-------------|-----------|-----------|-----------------|--------------------------|-------------------------|
| Code / behaviour change | ✅ | ✅ | ✅ update affected tests | if physical restore changes | if logical restore changes |
| Config field added / removed | ✅ config reference + examples | ✅ Known Behaviours if non-obvious | — | — | — |
| New CLI flag or entry point | ✅ section 5 + 8 | ✅ CLI Entry Points | ✅ add test | — | — |
| Credential / auth change | ✅ setup + cron sections | ✅ Known Behaviours | ✅ remove/update env var in commands | — | ✅ auth note + commands |
| Physical restore change | — | ✅ Documentation table | — | ✅ Step 1 dir list + Tested Environment | — |
| Logical restore change | — | ✅ Documentation table | — | — | ✅ Step 1 dir list + Tested Environment |
| New how-to guide | ✅ add row to §7 table | ✅ add row to Documentation table | — | — | — |

### Per-document rules

**`README.md`** — end-user docs. Keep in sync with the live deployment on `mysql-box`:
- Every config example must match what actually works on `mysql-box`
- Every command shown must be runnable as-is (no `export MYSQL_BACKUP_PASSWORD`, no missing flags)
- Every new `##` section must have a `[↑ Back to top](#table-of-contents)` link at the bottom, placed immediately above the `---` divider
- The Table of Contents must list every `##` section

**`CLAUDE.md`** — developer context for Claude. Update when:
- Architecture or job selection flow changes
- A new non-obvious behaviour or constraint is discovered
- A new module is added to Key Modules
- The credential resolution order or auth mechanism changes
- The live deployment environment changes (VM names, paths, versions)

**`docs/testing.md`** — live test record with real captured output. Update when:
- Any command's syntax changes (flags, env vars, prefixes)
- A new test is added or an existing test result changes
- The pre-test environment state changes (MySQL version, disk, config snapshot)

**`docs/restore-physical.md`** — physical backup restore runbook. Update when:
- The backup directory list in Step 1 example output changes
- The Tested Environment table needs a new date or backup path
- Any command in the procedure changes
- A new troubleshooting case is discovered during a restore

**`docs/restore-logical.md`** — logical backup restore from offsite runbook. Update when:
- The offsite backup directory path or timestamp changes
- myloader version changes (re-test the empty-db edge case)
- Auth method on the restore target changes (socket vs password)
- A new troubleshooting case is discovered during a restore
