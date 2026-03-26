# How to Do Point-in-Time Recovery Using Binlogs

Use this when you need to recover to a specific time (e.g. just before a bad `DROP TABLE`), combining a physical (or logical) backup with binlog backups.

---

## Step 1 — Restore the most recent physical backup before the incident

See [restore-physical.md](restore-physical.md) for the full restore procedure.

---

## Step 2 — Find the binlog position in the backup

The backup directory contains `xtrabackup_binlog_info` with the exact binlog position recorded at backup time. Read it directly from the backup directory — you do not need to wait until after `--copy-back`:

```bash
cat /var/backups/mysql/prod-mysql1/physical/20260322-010000/xtrabackup_binlog_info
# binlog.000072   157
```

This tells you: start replaying binlogs from file `binlog.000072` at position `157`.

---

## Step 3 — Replay binlogs up to the incident

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
