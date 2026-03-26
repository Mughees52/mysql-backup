# Conversation Log

Chronological record of development sessions. Read this at the start of any new session to understand project history, decisions, and current state.

---

## Session 1 — Initial build and full end-to-end test (2026-03-25)

### What was built
- Complete `mysql_backup/` Python package from scratch: logical, physical, and binlog backup types
- All core modules: `driver.py`, `config.py`, `backup_logical.py`, `backup_physical.py`, `backup_binlog.py`, `storage_local.py`, `storage_remote.py`, `mysql_client.py`, `encryption.py`, `dedup.py`, `checks.py`, `shell_utils.py`, `precheck.py`
- GASCAN-parity features added: replica gates (`replica_only`, `read_only_only`), kill long-running queries before backup, Azure Blob Storage offsite target, weekly retention tier, graceful stop sentinel file
- `--run-scheduled` flag in `driver.py` using `croniter` — replaces per-job cron entries with a single `* * * * *` cron line
- Live deployment on Multipass VM `mysql-box`: venv at `/root/mysql-backup-venv`, config at `/root/.config/mysql-backup/config.yml`

### Environment
- `mysql-box`: Ubuntu 22.04, MySQL 8.0.42, mydumper 0.10.0, xtrabackup 8.0.35-35
- `proxysql`: Ubuntu 22.04, MySQL 8.0.42, myloader 0.10.0 — used as offsite restore target
- Mac host (192.168.2.1): local dev machine running Multipass
- VM IPs: mysql-box = 192.168.2.2, proxysql = 192.168.2.3
- No direct VM-to-VM file transfer in Multipass — all transfers go VM → Mac → VM (two `multipass transfer` hops)

### Tests run (15 total — see docs/testing.md)
1. Config validation (`--validate-config`)
2. Self-test (`--self-test`)
3. List jobs (`--list-jobs`)
4. Precheck (`mysql_backup_precheck`)
5. Dry-run scheduled (`--run-scheduled --dry-run`)
6. Logical backup (`--job logical-daily`)
7. Physical backup with AES-256 encrypt + decrypt + prepare (`--job physical-daily`)
8. Binlog backup (`--job binlog-5min`)
9. Retention enforcement (daily + weekly purge)
10. Lock file (concurrent run prevention)
11. Graceful stop sentinel
12. `--run-scheduled` dispatch
13. Rsync offsite upload to proxysql
14. Logical restore from offsite using myloader
15. Physical restore to proxysql using xtrabackup `--copy-back`

---

## Session 2 — Bug fixes, credential cleanup, and documentation (2026-03-25)

### Physical backup restore (docs/restore-physical.md)
- Performed full physical backup restore: mysql-box → Mac host → proxysql
- Transfer pattern: `multipass transfer mysql-box:/path /tmp/file` then `multipass transfer /tmp/file proxysql:/path`
- xtrabackup installed on proxysql via the xtrabackup apt package (8.0.35-35)
- Restore steps: transfer tarball → extract → `xtrabackup --copy-back` → `chown -R mysql:mysql` → start MySQL
- Encrypted backup: `.xbcrypt` files remain alongside plain decrypted files after the pipeline runs; `--copy-back` ignores `.xbcrypt` and copies only plain files — no extra decrypt step needed at restore time
- Confirmed `xtrabackup_checkpoints` shows `full-prepared` before restore

### Credential change — removed MYSQL_BACKUP_PASSWORD
**Problem:** All CLI commands and the cron entry required `export MYSQL_BACKUP_PASSWORD=...` prefix, which was inconvenient and exposed the password in process lists.

**Fix:** Modified `mysql_client.py` `get_connection()` to fall back to `read_default_file=~/.my.cnf` when neither `password` nor `password_env` is set in config. Credentials now stored in `/root/.my.cnf` [client] section on `mysql-box`.

**Impact:** All test commands and the cron entry no longer need the env var prefix. Live config updated (removed `password_env: MYSQL_BACKUP_PASSWORD`, changed `user: backup` → `user: root`).

**Resolution order in mysql_client.py:**
1. `password` literal in config
2. `password_env` in config (reads named env var)
3. Neither set → reads `~/.my.cnf` via pymysql `read_default_file`

### rsync offsite bug — trailing slash
**Problem:** First rsync offsite upload dumped all 253 backup files flat into `/var/backups/mysql-offsite/` with no subdirectory — each run would overwrite the previous.

**Root cause:** `_push_rsync` in `storage_remote.py` was passing `local_path.rstrip("/") + "/"` — trailing slash on source makes rsync transfer directory *contents*, not the directory itself.

**Fix:** Removed the trailing slash from `local_path`. Now passes `local_path.rstrip("/")` so rsync transfers the timestamped directory (e.g. `20260325-142006/`) as a named subdirectory inside the destination root.

### rsync SSH setup on mysql-box
- Generated `id_ed25519` on mysql-box root: `ssh-keygen -t ed25519 -f /root/.ssh/id_ed25519 -N ""`
- Installed public key in `proxysql:/home/ubuntu/.ssh/authorized_keys`
- Created `/var/backups/mysql-offsite` on proxysql with `ubuntu` ownership
- Live rsync target in config: `ubuntu@192.168.2.3:/var/backups/mysql-offsite`

### Logical restore from offsite (docs/restore-logical.md)
Validated myloader restore from offsite copy on proxysql. Two bugs discovered and documented:

**Bug 1 — auth_socket access denied:** First myloader run used `--host=127.0.0.1 --password=...`. Failed: "Access denied for user 'root'@'localhost'". Root uses `auth_socket` on proxysql — must use `--socket=/var/run/mysqld/mysqld.sock` with no password, run as OS root via sudo.

**Bug 2 — myloader skips empty databases:** `mughees` database was absent after restore. myloader 0.10.0 silently skips databases that have only a `<db>-schema-create.sql.gz` file (no table data files). Fix: manually applied `zcat mughees-schema-create.sql.gz | mysql`. Documented in docs/restore-logical.md Step 5 and troubleshooting appendix.

### README navigation
Added `[↑ Back to top](#table-of-contents)` links after every `##` section in README.md to allow easy navigation back from any section.

---

## Session 3 — Documentation reorganisation and cleanup (2026-03-26)

### Docs folder created
Moved all documentation out of the root into `docs/`:

| Old location | New location |
|---|---|
| `TESTING.md` | `docs/testing.md` |
| `HOWTO-restore.md` | `docs/restore-physical.md` |
| `HOWTO-restore-logical.md` | `docs/restore-logical.md` |
| README §7 inline content | Split into individual files below |

New files extracted from README §7:

| File | Contents |
|---|---|
| `docs/setup-encryption.md` | AES-256 key generation, credentials file, job config |
| `docs/setup-binlog.md` | Enable binary logging, job config, REPLICATION SLAVE grant |
| `docs/pitr.md` | Point-in-time recovery using binlogs after a restore |
| `docs/verify-backup.md` | Verify `full-prepared` state, `--prepare --export` sanity check |
| `docs/operations.md` | Upgrade, multiple configs, graceful stop |

README §7 is now a clean 8-row link table pointing to `docs/`. CLAUDE.md documentation table and doc matrix updated to reference `docs/` paths.

### Branding cleanup
Removed all references to "GASCAN" and "Percona" from user-facing text:

| File | Change |
|---|---|
| `README.md` | "GASCAN-compliant" → "configurable daily + weekly tiers"; "Percona XtraDB Cluster" → "Galera/XtraDB Cluster" |
| `CLAUDE.md` | "GASCAN-compliant retention policies" → "configurable daily/weekly retention" |
| `mysql_backup/checks.py` | Removed "GASCAN recommendation" from docstring |
| `mysql_backup/storage_local.py` | Removed "GASCAN MYDUMPER_WEEKLY_PURGE" from docstring |
| `mysql_backup/backup_binlog.py` | Removed "GASCAN BINLOG_DISK_FREE_PCT" from comment |
| `docs/restore-physical.md` | Changed comment from "add the Percona repository" to "install xtrabackup 8.0" |

Note: the actual xtrabackup install commands still reference `repo.percona.com` and `percona-xtrabackup-80` — these are the real package names and cannot be changed. Only the descriptive labels were cleaned up.

---

## Current system state (as of 2026-03-26)

### Live deployment on mysql-box
- Config: `/root/.config/mysql-backup/config.yml`
- Credentials: `/root/.my.cnf` (no password in config or env var)
- xtrabackup credentials: `/root/.config/mysql-backup/xtrabackup.cnf`
- Encryption key: `/root/.secrets/xtrabackup.key` (32-byte binary, AES-256)
- Cron: `/etc/cron.d/mysql-backup` — single `* * * * *` line, no env var prefix
- Jobs configured: `logical-daily`, `physical-daily`, `binlog-5min`
- Offsite: rsync to `ubuntu@192.168.2.3:/var/backups/mysql-offsite` via `/root/.ssh/id_ed25519`
- Log dir: `/var/log/mysql-backup/`
- Backup root: `/var/backups/mysql/`

### Offsite state on proxysql
- Directory: `/var/backups/mysql-offsite/`
- Latest backup: `20260325-142006/` (253 files, 1.3 MB compressed)
- MySQL instance on proxysql: 8.0.42, root uses `auth_socket`

### Repository structure
```
mysql_backup/          Python package (single namespace)
docs/                  All documentation and runbooks
  testing.md           15-test end-to-end record
  restore-physical.md  Physical backup restore runbook (validated 2026-03-25)
  restore-logical.md   Logical backup restore runbook (validated 2026-03-25)
  setup-encryption.md  AES-256 setup guide
  setup-binlog.md      Binlog backup setup guide
  pitr.md              Point-in-time recovery guide
  verify-backup.md     Backup verification guide
  operations.md        Upgrade, multi-config, graceful stop
  conversationlog.md   This file
README.md              End-user documentation
CLAUDE.md              Developer context for Claude Code sessions
config.yaml            Example config
```

### Known quirks to remember
- myloader 0.10.0 silently skips databases with no table data files — always check `SHOW DATABASES` after a logical restore and manually apply any missing `<db>-schema-create.sql.gz`
- proxysql root uses `auth_socket` — use `sudo myloader --socket=...`, not `--password`
- rsync source must have NO trailing slash — trailing slash transfers contents flat, not the directory
- xtrabackup `--copy-back` ignores `.xbcrypt` files automatically — no manual decrypt needed at restore time if `prepare_after_backup: true` was used
- Multipass has no direct VM-to-VM transfer — always go VM → Mac host (`/tmp/`) → VM
