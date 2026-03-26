# How to Set Up Binlog Backups

Binlog backups stream binlog events from MySQL using `mysqlbinlog --read-from-remote-server`. They provide point-in-time recovery capability between physical/logical backups.

---

## Step 1 — Check binary logging is enabled

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

---

## Step 2 — Check the current binlog filename

MySQL 8.0 uses `binlog.XXXXXX` as the default prefix (not `mysql-bin`). Check what your server uses:

```sql
SHOW BINARY LOGS;
-- Example output:
-- binlog.000033  201
-- binlog.000034  201
-- binlog.000068  157   ← this is the current file
```

Use the **oldest available** file as the starting point for your first run.

---

## Step 3 — Configure the job

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

---

## Step 4 — Ensure the backup user has REPLICATION SLAVE

```sql
GRANT REPLICATION SLAVE ON *.* TO 'backup'@'localhost';
FLUSH PRIVILEGES;
```

This is required for `mysqlbinlog --read-from-remote-server`. Without it you will get:
```
ERROR: Got error reading packet from server: Access denied; you need (at least one of) the REPLICATION SLAVE privilege(s)
```
