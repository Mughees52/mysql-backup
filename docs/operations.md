# Operations Guide

Common operational tasks: upgrading the tool, running multiple configs, and graceful stop.

---

## How to upgrade the tool

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

## How to run multiple configs on the same host

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

## How to use graceful stop

The driver checks the sentinel file **before starting each job**, not after finishing one. This means:

- If the sentinel file exists **before** the driver is invoked, **no jobs run** and the driver exits immediately.
- If the sentinel file is **created while a job is running**, that job runs to completion, then the driver stops before starting the next one.

First, configure `graceful_stop_file` in your `config.yml`:

```yaml
global:
  graceful_stop_file: /root/.config/mysql-backup/GRACEFUL_STOP
```

Then to trigger a clean stop:

```bash
# Create the sentinel — driver will halt at the next between-job checkpoint
touch /root/.config/mysql-backup/GRACEFUL_STOP

# Remove the file before the next cron invocation or it will block all jobs
rm /root/.config/mysql-backup/GRACEFUL_STOP
```

To auto-remove after 5 minutes (create and self-clean):

```bash
touch /root/.config/mysql-backup/GRACEFUL_STOP
(sleep 300 && rm -f /root/.config/mysql-backup/GRACEFUL_STOP) &
```

Log output when graceful stop is triggered:
```
[INFO]    Starting backup run
[INFO]    Graceful stop requested - halting before next job
```
