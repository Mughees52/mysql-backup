# How to Verify a Physical Backup Without Restoring

After a successful backup job with `prepare_after_backup: true`, the backup is already in `full-prepared` state. You can confirm this instantly:

```bash
BACKUP_DIR=/var/backups/mysql/prod-mysql1/physical/20260322-010000

cat "$BACKUP_DIR/xtrabackup_checkpoints"
# backup_type = full-prepared   ← confirms apply-log completed successfully
# from_lsn = 0
# to_lsn = 36199564
```

For a deeper InnoDB consistency check, use `--prepare --export`. This makes individual tablespaces exportable and acts as a sanity check — if InnoDB pages are corrupt it will fail:

```bash
xtrabackup --prepare --export --target-dir="$BACKUP_DIR"
# Should end with: "completed OK!"
```

> **Important:** Do not run plain `xtrabackup --prepare` (without `--export`) on an already-prepared backup — it will fail with `This target seems to be already prepared`. Always use `--prepare --export` if re-running prepare manually.

You can enable the `--prepare --export` check automatically after every backup by setting `verify_after_backup: true` in the job's `backup_options`.
