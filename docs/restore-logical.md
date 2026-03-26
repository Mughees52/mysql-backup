# How To: Restore a Logical Backup from the Offsite Remote Server

This guide covers a complete, tested restore of the latest mydumper logical backup
from the offsite copy on `proxysql` back into a running MySQL instance.

The logical backup is uploaded to `proxysql` by the `logical-daily` job via rsync after every run.
It is the backup you reach for when you need selective database or table recovery, or when
cross-version migration is required. For a full server clone use
[HOWTO-restore.md](HOWTO-restore.md) (physical backup).

---

## Table of Contents

1. [Overview](#1-overview)
2. [Prerequisites](#2-prerequisites)
3. [Step 1 — Find the latest offsite backup](#3-step-1--find-the-latest-offsite-backup)
4. [Step 2 — Inspect the backup metadata](#4-step-2--inspect-the-backup-metadata)
5. [Step 3 — Install myloader on the restore target](#5-step-3--install-myloader-on-the-restore-target)
6. [Step 4 — Run myloader to restore](#6-step-4--run-myloader-to-restore)
7. [Step 5 — Restore empty databases manually](#7-step-5--restore-empty-databases-manually)
8. [Step 6 — Verify the restore](#8-step-6--verify-the-restore)
9. [Appendix A — Restore a single database only](#9-appendix-a--restore-a-single-database-only)
10. [Appendix B — Point-in-time recovery with binlogs](#10-appendix-b--point-in-time-recovery-with-binlogs)
11. [Appendix C — Troubleshooting](#11-appendix-c--troubleshooting)

---

## 1. Overview

The `logical-daily` job runs mydumper and rsyncs the output to `proxysql` after every backup:

```
mysql-box:/var/backups/mysql/mysql-box/logical/<timestamp>/  →  rsync  →  proxysql:/var/backups/mysql-offsite/<timestamp>/
```

Each backup directory contains:

| File pattern | Contents |
|---|---|
| `metadata` | Dump start/end time and binlog position at backup |
| `<db>-schema-create.sql.gz` | `CREATE DATABASE` statement for each database |
| `<db>.<table>-schema.sql.gz` | `CREATE TABLE` statement for each table |
| `<db>.<table>.sql.gz` | Row data for each table |

The restore tool is **myloader**, the counterpart to mydumper.

---

## 2. Prerequisites

| Requirement | Detail |
|---|---|
| MySQL running on restore target | Must be up and accessible |
| myloader installed | Version must be ≥ 0.10.0 (see Step 3) |
| Enough free disk space | ≥ 2× the compressed backup size (1.3 MB → ≥ 3 MB) |
| Root or sudo access | Required |
| OS user running restore | Must match MySQL auth method (see Step 4 note) |

> **Auth note:** The MySQL `root` user on this deployment uses `auth_socket`.
> Run myloader as the OS `root` user (via `sudo`) with `--socket` — no password needed.
> If your target uses password auth, substitute `--password=<pass>` for `--socket`.

---

## 3. Step 1 — Find the latest offsite backup

```bash
# On proxysql — list all offsite backup directories, newest last
ls /var/backups/mysql-offsite/ | sort
```

Example output:
```
20260325-142006
```

Note the directory name — used in every subsequent command:

```
BACKUP_DIR=/var/backups/mysql-offsite/20260325-142006
```

---

## 4. Step 2 — Inspect the backup metadata

```bash
# On proxysql
cat /var/backups/mysql-offsite/20260325-142006/metadata
```

Expected output:
```
Started dump at: 2026-03-25 14:20:06
SHOW MASTER STATUS:
	Log: binlog.000079
	Pos: 157
	GTID:

Finished dump at: 2026-03-25 14:20:07
```

Note the binlog position (`binlog.000079:157`) — needed if you apply binlog events for
point-in-time recovery after the restore (see [Appendix B](#10-appendix-b--point-in-time-recovery-with-binlogs)).

List the databases included in the backup:
```bash
ls /var/backups/mysql-offsite/20260325-142006/*-schema-create.sql.gz
```

Example output:
```
mughees-schema-create.sql.gz
mysql-schema-create.sql.gz
```

---

## 5. Step 3 — Install myloader on the restore target

```bash
# On proxysql — install mydumper package (includes myloader)
sudo apt-get install -y mydumper
```

Verify:
```bash
myloader --version
```

Expected output:
```
myloader 0.10.0, built against MySQL 8.0.23
```

---

## 6. Step 4 — Run myloader to restore

This restores all databases and tables from the backup into the running MySQL instance.
`--overwrite-tables` drops and recreates each table before loading — safe to run against
an existing instance.

```bash
# On proxysql — run as root OS user (auth_socket, no password)
sudo myloader \
  --socket=/var/run/mysqld/mysqld.sock \
  --user=root \
  --directory=/var/backups/mysql-offsite/20260325-142006 \
  --overwrite-tables \
  --threads=4 \
  --verbose=3
```

If your MySQL uses password auth instead of auth_socket:
```bash
sudo myloader \
  --host=127.0.0.1 \
  --user=root \
  --password=your_root_password \
  --directory=/var/backups/mysql-offsite/20260325-142006 \
  --overwrite-tables \
  --threads=4 \
  --verbose=3
```

The output streams a line per table as each is dropped, created, and loaded. When complete,
the last line will be silent (no "completed OK" message — successful exit is exit code 0).

Check the exit code:
```bash
echo $?
```

Expected: `0`

---

## 7. Step 5 — Restore empty databases manually

> **myloader 0.10.0 behaviour:** databases that have no tables in the backup (only a
> `<db>-schema-create.sql.gz` file) are silently skipped by myloader. You must apply those
> schema-create files manually.

Identify which databases have only a schema-create file and no table data:
```bash
# List databases in backup
ls /var/backups/mysql-offsite/20260325-142006/*-schema-create.sql.gz \
  | sed 's|.*\/||; s|-schema-create.sql.gz||'
```

Example output:
```
mughees
mysql
```

For each database that is missing after the restore, apply its schema-create:
```bash
# Verify which databases are present after myloader
sudo mysql -e "SHOW DATABASES;"

# For any missing database, run:
sudo bash -c 'zcat /var/backups/mysql-offsite/20260325-142006/mughees-schema-create.sql.gz | mysql'
```

Confirm it now exists:
```bash
sudo mysql -e "SHOW DATABASES;"
```

Expected — `mughees` now appears:
```
+--------------------+
| Database           |
+--------------------+
| information_schema |
| mughees            |
| mysql              |
| performance_schema |
| sys                |
+--------------------+
```

---

## 8. Step 6 — Verify the restore

### Check 1 — All expected databases present

```bash
sudo mysql -e "SHOW DATABASES;"
```

Compare against the source (`mysql-box`):
```bash
# On mysql-box
sudo mysql -e "SHOW DATABASES;"
```

### Check 2 — Table counts match source

```bash
# On proxysql
sudo mysql -e "
  SELECT table_schema, COUNT(*) AS tables
  FROM information_schema.tables
  WHERE table_schema NOT IN ('information_schema','performance_schema')
  GROUP BY table_schema
  ORDER BY table_schema;"
```

Expected:
```
+--------------+--------+
| table_schema | tables |
+--------------+--------+
| mughees      |      0 |
| mysql        |     37 |
| sys          |    101 |
+--------------+--------+
```

### Check 3 — User accounts restored

```bash
sudo mysql -e "SELECT user, host FROM mysql.user ORDER BY user;"
```

Expected — all source accounts present:
```
+--------------------+-----------+
| user               | host      |
+--------------------+-----------+
| backup             | localhost |
| debian-sys-maint   | localhost |
| mysql.infoschema   | localhost |
| mysql.session      | localhost |
| mysql.sys          | localhost |
| pcs_test           | localhost |
| root               | localhost |
+--------------------+-----------+
```

### Check 4 — Binlog position anchor recorded

```bash
cat /var/backups/mysql-offsite/20260325-142006/metadata | grep -E "Log|Pos"
```

Expected:
```
	Log: binlog.000079
	Pos: 157
```

---

## 9. Appendix A — Restore a single database only

To restore just one database from the backup without touching others:

```bash
sudo myloader \
  --socket=/var/run/mysqld/mysqld.sock \
  --user=root \
  --directory=/var/backups/mysql-offsite/20260325-142006 \
  --database=mughees \
  --overwrite-tables \
  --threads=4 \
  --verbose=3
```

If the target database does not yet exist, create it first:
```bash
sudo bash -c 'zcat /var/backups/mysql-offsite/20260325-142006/mughees-schema-create.sql.gz | mysql'
```

---

## 10. Appendix B — Point-in-time recovery with binlogs

The metadata file records the exact binlog position when the dump was taken. To recover
data written after the backup, apply binlog events from that position forward.

Binlogs are stored on `mysql-box` at:
```
/var/backups/mysql/mysql-box/binlog/
```

Transfer the relevant binlog file to the restore target (two-hop via Mac host):

```bash
# On mysql-box — find the binlog file recorded in metadata (binlog.000079)
sudo ls /var/backups/mysql/mysql-box/binlog/

# On Mac host — transfer to proxysql
multipass transfer mysql-box:/var/backups/mysql/mysql-box/binlog/<latest-dir>/binlog.sql \
                   /tmp/binlog.sql
multipass transfer /tmp/binlog.sql proxysql:/tmp/binlog.sql
```

Apply from the recorded position:
```bash
# On proxysql
sudo mysqlbinlog \
  --start-position=157 \
  /tmp/binlog.sql \
  | sudo mysql
```

To stop at a specific point in time:
```bash
sudo mysqlbinlog \
  --start-position=157 \
  --stop-datetime="2026-03-25 15:00:00" \
  /tmp/binlog.sql \
  | sudo mysql
```

---

## 11. Appendix C — Troubleshooting

### myloader: "Access denied for user 'root'@'localhost'"

Root uses `auth_socket` — connect via socket without a password:
```bash
sudo myloader --socket=/var/run/mysqld/mysqld.sock --user=root ...
```

### A database is missing after myloader completes

myloader 0.10.0 silently skips databases that have no table data files (only a
`-schema-create.sql.gz`). Apply the schema-create manually:
```bash
sudo bash -c 'zcat /var/backups/mysql-offsite/<timestamp>/<db>-schema-create.sql.gz | mysql'
```

### myloader: "Table X already exists"

Add `--overwrite-tables` to the myloader command — it will `DROP TABLE IF EXISTS` before
recreating each table.

### myloader hangs or is very slow

Reduce `--threads` to `1` to rule out lock contention:
```bash
sudo myloader --socket=... --threads=1 ...
```

### "No backup files found" or empty restore

Confirm the directory path is correct and contains `.sql.gz` files:
```bash
ls /var/backups/mysql-offsite/<timestamp>/*.sql.gz | wc -l
```

Expected: > 0 (this deployment has 253 files).

---

## Tested Environment

| Item | Value |
|---|---|
| Date tested | 2026-03-25 |
| Offsite backup location | `proxysql:/var/backups/mysql-offsite/20260325-142006` |
| Backup source | `mysql-box` logical-daily (mydumper, compressed) |
| Backup size | 253 files, 1.3 MB |
| Binlog position | `binlog.000079:157` |
| Restore target | `proxysql` — Ubuntu 22.04, MySQL 8.0.42, myloader 0.10.0 |
| Auth method | `auth_socket` — `sudo myloader --socket=...` |
| Result | All tables and user accounts restored; empty `mughees` db created manually via schema-create |
