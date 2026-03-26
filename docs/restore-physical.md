# How To: Restore a MySQL Physical Backup on Another Server

This guide covers a complete, tested restore of the latest `physical-daily` backup from `mysql-box`
onto a fresh target server (demonstrated against `proxysql`).

**Backup type:** xtrabackup full physical backup, AES-256 encrypted, decrypt + prepare already
applied by the backup pipeline (`prepare_after_backup: true`).

---

## Table of Contents

1. [Overview](#1-overview)
2. [Prerequisites](#2-prerequisites)
3. [Step 1 — Find the latest backup on the source server](#3-step-1--find-the-latest-backup-on-the-source-server)
4. [Step 2 — Verify the backup is ready to restore](#4-step-2--verify-the-backup-is-ready-to-restore)
5. [Step 3 — Install xtrabackup on the target server](#5-step-3--install-xtrabackup-on-the-target-server)
6. [Step 4 — Transfer the backup to the target server](#6-step-4--transfer-the-backup-to-the-target-server)
7. [Step 5 — Stop MySQL on the target server](#7-step-5--stop-mysql-on-the-target-server)
8. [Step 6 — Clear the MySQL data directory](#8-step-6--clear-the-mysql-data-directory)
9. [Step 7 — Run xtrabackup --copy-back](#9-step-7--run-xtrabackup---copy-back)
10. [Step 8 — Fix ownership and start MySQL](#10-step-8--fix-ownership-and-start-mysql)
11. [Step 9 — Verify the restore](#11-step-9--verify-the-restore)
12. [Appendix A — If the backup is NOT yet decrypted/prepared](#12-appendix-a--if-the-backup-is-not-yet-decryptedprepared)
13. [Appendix B — Point-in-time recovery with binlogs](#13-appendix-b--point-in-time-recovery-with-binlogs)
14. [Appendix C — Troubleshooting](#14-appendix-c--troubleshooting)

---

## 1. Overview

The backup pipeline on `mysql-box` produces a three-stage artifact for every `physical-daily` job:

```
Stage 1 — xtrabackup --backup          → AES-256 encrypted .xbcrypt files
Stage 2 — xtrabackup --decrypt=AES256  → decrypted plain files written alongside .xbcrypt files
Stage 3 — xtrabackup --prepare         → redo log applied; backup_type becomes full-prepared
```

Because the pipeline runs all three stages automatically, the backup directory on disk is
**already decrypted and prepared** by the time you come to restore it. No decrypt or prepare
step is required on the target server.

If your backup was taken with `prepare_after_backup: false` (raw encrypted backup), see
[Appendix A](#12-appendix-a--if-the-backup-is-not-yet-decryptedprepared).

---

## 2. Prerequisites

| Requirement | Source server (`mysql-box`) | Target server (`proxysql`) |
|-------------|----------------------------|---------------------------|
| OS | Ubuntu 24.04 | Ubuntu 22.04 |
| MySQL version | 8.0.45 | 8.0.45 (must match major.minor) |
| xtrabackup | 8.0.35-35 | 8.0.35-35 (must match) |
| Free disk | — | ≥ 2× the backup size (backup is 196 MB → need ≥ 400 MB free) |
| Root / sudo | required | required |
| Network | reachable from host or intermediate | reachable from host or intermediate |

> **MySQL version must match.** xtrabackup restores are not cross-version safe.
> If `mysql-box` runs 8.0.45, `proxysql` must also run 8.0.45.

---

## 3. Step 1 — Find the latest backup on the source server

```bash
# On mysql-box — list all physical backup directories, newest last
sudo find /var/backups/mysql/mysql-box/physical -mindepth 1 -maxdepth 1 -type d | sort
```

Example output:
```
/var/backups/mysql/mysql-box/physical/20260322-154520
/var/backups/mysql/mysql-box/physical/20260325-124139
```

The **last line** is the latest backup. Note the directory name — you will use it in every
subsequent command. In this guide the latest backup is:

```
BACKUP_DIR=/var/backups/mysql/mysql-box/physical/20260325-124139
```

---

## 4. Step 2 — Verify the backup is ready to restore

The backup must be in `full-prepared` state before it can be restored.

```bash
# On mysql-box
sudo cat /var/backups/mysql/mysql-box/physical/20260325-124139/xtrabackup_checkpoints
```

Expected output — **backup_type must be `full-prepared`**:
```
backup_type = full-prepared
from_lsn = 0
to_lsn = 36200260
last_lsn = 36200260
flushed_lsn = 36200260
```

Also confirm the binlog position (useful for point-in-time recovery):
```bash
sudo grep binlog_pos /var/backups/mysql/mysql-box/physical/20260325-124139/xtrabackup_info
```

Example output:
```
binlog_pos = filename 'binlog.000079', position '157'
```

> If `backup_type` is `full-backuped` (not `full-prepared`), the redo log has not been applied.
> See [Appendix A](#12-appendix-a--if-the-backup-is-not-yet-decryptedprepared) before continuing.

---

## 5. Step 3 — Install xtrabackup on the target server

The xtrabackup version on the target must match the version used to take the backup
(both are 8.0.35-35 in this environment).

```bash
# On proxysql — install xtrabackup 8.0
sudo wget -q https://repo.percona.com/apt/percona-release_latest.generic_all.deb \
     -O /tmp/percona-release.deb

sudo dpkg -i /tmp/percona-release.deb

sudo percona-release setup pxb-80 -y

sudo apt-get install -y percona-xtrabackup-80
```

Verify:
```bash
xtrabackup --version
```

Expected output:
```
xtrabackup version 8.0.35-35 based on MySQL server 8.0.35 Linux (aarch64) (revision id: be447639)
```

---

## 6. Step 4 — Transfer the backup to the target server

The backup directory (196 MB) must be copied from `mysql-box` to `proxysql`. The two VMs
communicate over `192.168.2.0/24` but Multipass does not expose direct VM-to-VM file transfer,
so the transfer goes through the Mac host as an intermediate.

### 4a — Archive the backup on mysql-box

```bash
# On mysql-box — create a compressed tarball of the backup directory
sudo tar czf /tmp/physical-backup-20260325.tar.gz \
     -C /var/backups/mysql/mysql-box/physical \
     20260325-124139
```

Verify the archive was created:
```bash
ls -lh /tmp/physical-backup-20260325.tar.gz
```

### 4b — Transfer from mysql-box to the Mac host

```bash
# On Mac host
multipass transfer mysql-box:/tmp/physical-backup-20260325.tar.gz \
                   /tmp/physical-backup-20260325.tar.gz
```

### 4c — Transfer from Mac host to proxysql

```bash
# On Mac host
multipass transfer /tmp/physical-backup-20260325.tar.gz \
                   proxysql:/tmp/physical-backup-20260325.tar.gz
```

### 4d — Extract on proxysql

```bash
# On proxysql
sudo mkdir -p /var/backups/mysql-restore

sudo tar xzf /tmp/physical-backup-20260325.tar.gz \
     -C /var/backups/mysql-restore

# Confirm extraction
ls /var/backups/mysql-restore/
```

Expected output:
```
20260325-124139
```

---

## 7. Step 5 — Stop MySQL on the target server

```bash
# On proxysql
sudo systemctl stop mysql

# Confirm MySQL is stopped
sudo systemctl status mysql | head -4
```

Expected output:
```
● mysql.service - MySQL Community Server
   ...
   Active: inactive (dead)
```

---

## 8. Step 6 — Clear the MySQL data directory

> **Warning:** This permanently removes all existing data on the target server.
> If you need to preserve the current data, take a backup first or move it aside.

```bash
# On proxysql — confirm what is currently in the data directory
sudo ls /var/lib/mysql/

# Remove all existing MySQL data
sudo rm -rf /var/lib/mysql/*

# Confirm the directory is empty
sudo ls /var/lib/mysql/ | wc -l
```

Expected output: `0`

---

## 9. Step 7 — Run xtrabackup --copy-back

This copies the backup files from the staging area into the MySQL data directory.

```bash
# On proxysql
sudo xtrabackup \
     --copy-back \
     --target-dir=/var/backups/mysql-restore/20260325-124139 \
     --datadir=/var/lib/mysql
```

The command will stream lines like:
```
[Note] [Xtrabackup] Copying ./ibdata1 to /var/lib/mysql/ibdata1
[Note] [Xtrabackup] Done: Copying ./ibdata1 to /var/lib/mysql/ibdata1
...
```

The final line **must be**:
```
[Note] [Xtrabackup] completed OK!
```

> xtrabackup automatically skips `.xbcrypt` files (the encrypted originals left on disk from
> the decrypt step). Only the decrypted plain files are copied into the data directory.

---

## 10. Step 8 — Fix ownership and start MySQL

The files copied by xtrabackup are owned by `root`. MySQL needs them owned by `mysql:mysql`.

```bash
# On proxysql — fix ownership
sudo chown -R mysql:mysql /var/lib/mysql

# Start MySQL
sudo systemctl start mysql

# Confirm MySQL is running
sudo systemctl status mysql | head -5
```

Expected output:
```
● mysql.service - MySQL Community Server
   Active: active (running) since ...
```

---

## 11. Step 9 — Verify the restore

Run each check and compare against the source server.

### Check 1 — MySQL version

```bash
# On proxysql
sudo mysql -e "SELECT @@version;"
```

Expected:
```
+---------------------------------+
| @@version                       |
+---------------------------------+
| 8.0.45-0ubuntu0.22.04.1         |
+---------------------------------+
```

### Check 2 — Databases present

```bash
# On proxysql
sudo mysql -e "SHOW DATABASES;"
```

Compare with the source:
```bash
# On mysql-box
sudo mysql -e "SHOW DATABASES;"
```

Both should list the same databases.

### Check 3 — Table count per schema

```bash
# On proxysql
sudo mysql -e "SELECT table_schema, COUNT(*) AS tables
               FROM information_schema.tables
               GROUP BY table_schema
               ORDER BY table_schema;"
```

### Check 4 — User accounts restored

```bash
# On proxysql
sudo mysql -e "SELECT user, host FROM mysql.user ORDER BY user;"
```

### Check 5 — InnoDB integrity

```bash
# On proxysql
sudo mysql -e "SELECT * FROM information_schema.INNODB_TABLESPACES LIMIT 5;"
```

No errors should be returned.

### Check 6 — Confirm binlog position recorded

```bash
# On proxysql
sudo cat /var/lib/mysql/xtrabackup_info | grep binlog_pos
```

Expected:
```
binlog_pos = filename 'binlog.000079', position '157'
```

This is the point-in-time anchor if you need to apply binlog events after the restore
(see [Appendix B](#13-appendix-b--point-in-time-recovery-with-binlogs)).

---

## 12. Appendix A — If the backup is NOT yet decrypted/prepared

Use this appendix only if `xtrabackup_checkpoints` shows `backup_type = full-backuped`
(i.e., `prepare_after_backup: false` was set, or the backup was taken without the pipeline).

### A1 — Decrypt the backup in-place (on the target server, after transfer)

You need the same AES-256 key file that was used to take the backup. Copy it from mysql-box:

```bash
# On Mac host — copy the key file from mysql-box
multipass transfer mysql-box:/root/.secrets/xtrabackup.key \
                   /tmp/xtrabackup.key

# Transfer to proxysql
multipass transfer /tmp/xtrabackup.key proxysql:/tmp/xtrabackup.key
```

```bash
# On proxysql — run decrypt in-place
sudo xtrabackup \
     --decrypt=AES256 \
     --encrypt-key-file=/tmp/xtrabackup.key \
     --target-dir=/var/backups/mysql-restore/20260325-124139

# Remove the key file when done
sudo rm -f /tmp/xtrabackup.key
```

Decrypt creates plain versions of every `.xbcrypt` file in-place:
```
ibdata1.xbcrypt  →  ibdata1   (new decrypted file)
mysql.ibd.xbcrypt →  mysql.ibd
...
```

### A2 — Prepare the backup (apply redo log)

```bash
# On proxysql — prepare must NOT use --encrypt, only --prepare
sudo xtrabackup \
     --prepare \
     --target-dir=/var/backups/mysql-restore/20260325-124139
```

The final line must be:
```
[Note] [Xtrabackup] completed OK!
```

Verify:
```bash
sudo cat /var/backups/mysql-restore/20260325-124139/xtrabackup_checkpoints
```

`backup_type` must now read `full-prepared`. Then continue from
[Step 5](#7-step-5--stop-mysql-on-the-target-server) onwards.

---

## 13. Appendix B — Point-in-time recovery with binlogs

The physical backup captures a consistent snapshot. To recover up to a specific time or
transaction after the backup was taken, apply the binlog events that were streamed after
the backup's `binlog_pos`.

The backup recorded this position:
```
binlog_pos = filename 'binlog.000079', position '157'
```

The binlogs are stored on mysql-box at:
```
/var/backups/mysql/mysql-box/binlog/
```

### B1 — Extract binlog events from the backup position forward

```bash
# On mysql-box — extract SQL events starting from the known position
sudo mysqlbinlog \
     --start-position=157 \
     /var/backups/mysql/mysql-box/binlog/<latest-dir>/binlog.sql \
     > /tmp/binlog-replay.sql
```

Transfer `/tmp/binlog-replay.sql` to proxysql using the same
[two-hop transfer method](#6-step-4--transfer-the-backup-to-the-target-server) shown in Step 4.

### B2 — Apply binlog events on proxysql

```bash
# On proxysql — replay the extracted events
sudo mysql < /tmp/binlog-replay.sql
```

To stop at a specific point in time (e.g., before an accidental `DROP TABLE`):

```bash
sudo mysqlbinlog \
     --start-position=157 \
     --stop-datetime="2026-03-25 13:00:00" \
     /var/backups/mysql/mysql-box/binlog/<latest-dir>/binlog.sql \
     | sudo mysql
```

---

## 14. Appendix C — Troubleshooting

### MySQL fails to start after restore

Check the error log:
```bash
sudo tail -50 /var/log/mysql/error.log
```

**Common cause — wrong ownership:**
```bash
sudo chown -R mysql:mysql /var/lib/mysql
sudo systemctl start mysql
```

**Common cause — innodb_redo directory missing:**
```bash
sudo ls /var/lib/mysql/#innodb_redo
# If missing, xtrabackup may not have copied it; check copy-back output for errors
```

---

### xtrabackup --copy-back fails with "destination exists"

The data directory was not fully cleared. Re-run the clear:
```bash
sudo systemctl stop mysql
sudo rm -rf /var/lib/mysql/*
```

Then re-run the `--copy-back` command.

---

### xtrabackup --copy-back fails with "can't read backup-my.cnf"

The tarball extraction may have failed or the path is wrong. Verify:
```bash
ls /var/backups/mysql-restore/20260325-124139/backup-my.cnf
cat /var/backups/mysql-restore/20260325-124139/xtrabackup_checkpoints
```

---

### "Table 'X' doesn't exist" after restore

The backup may be partial (`partial = Y` in `xtrabackup_info`). Check:
```bash
grep partial /var/backups/mysql-restore/20260325-124139/xtrabackup_info
```

A full backup shows `partial = N`. If partial, re-run the backup with no `--tables` filter.

---

### Restoring to a server that will become a replica

After confirming the restore works:
1. Note the binlog position from `xtrabackup_info`: `binlog_pos = filename 'binlog.000079', position '157'`
2. On proxysql, configure replication:

```sql
CHANGE MASTER TO
  MASTER_HOST='192.168.2.2',
  MASTER_USER='replication_user',
  MASTER_PASSWORD='repl_pass',
  MASTER_LOG_FILE='binlog.000079',
  MASTER_LOG_POS=157;

START SLAVE;
SHOW SLAVE STATUS\G
```

---

## Tested Environment

| Item | Value |
|------|-------|
| Date tested | 2026-03-25 (validated end-to-end twice by following this document) |
| Source server | `mysql-box` — Ubuntu 24.04, MySQL 8.0.45, xtrabackup 8.0.35-35 |
| Target server | `proxysql` — Ubuntu 22.04, MySQL 8.0.45, xtrabackup 8.0.35-35 |
| Backup directory | `/var/backups/mysql/mysql-box/physical/20260325-124139` |
| Backup size | 196 MB (80 MB compressed tarball) |
| Backup type | `full-prepared` (AES-256 decrypted + redo log applied by pipeline) |
| Binlog position | `binlog.000079:157` |
| Databases restored | information_schema, mughees, mysql, performance_schema, sys |
| User accounts restored | backup, debian-sys-maint, pcs_test, root (+ 3 system users) |
| Result | MySQL started clean, all databases and user accounts matched source exactly |
