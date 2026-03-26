# How to Set Up Physical Backups with AES-256 Encryption

Physical backups can be encrypted with xtrabackup's built-in AES-256. The encrypted backup is useless without the key, so store it securely.

---

## Step 1 — Generate the key

xtrabackup expects a **raw 32-byte binary key** file (not hex-encoded):

```bash
mkdir -p /root/.secrets
openssl rand -out /root/.secrets/xtrabackup.key 32
chmod 600 /root/.secrets/xtrabackup.key
# Verify: should print "32 /root/.secrets/xtrabackup.key"
wc -c /root/.secrets/xtrabackup.key
```

> **Common mistake:** `openssl rand -hex 32` generates a 64-character hex string, not a 32-byte binary key. xtrabackup will reject it with `Invalid key length`. Always use `openssl rand -out <file> 32`.

---

## Step 2 — Create an xtrabackup credentials file

Instead of passing `--password=` on the command line (which appears in `ps` output), store xtrabackup credentials in a dedicated config file:

```bash
cat > /root/.config/mysql-backup/xtrabackup.cnf << 'EOF'
[xtrabackup]
user=backup
password=s3cr3t
host=localhost
port=3306
EOF
chmod 600 /root/.config/mysql-backup/xtrabackup.cnf
```

---

## Step 3 — Reference both in your job config

```yaml
jobs:
  - name: physical-daily
    instance: prod-mysql1
    type: physical
    backup_options:
      tool: xtrabackup
      xtrabackup_path: /usr/bin/xtrabackup
      backup_mode: full
      prepare_after_backup: true
      defaults_file: /root/.config/mysql-backup/xtrabackup.cnf
      use_xtra_encryption: true
      xtra_key_file: /root/.secrets/xtrabackup.key
      xtra_encrypt_algo: AES256
    encryption: xtrabackup_aes256
```

---

## How prepare works with encryption

When `prepare_after_backup: true`, the driver automatically:
1. Runs `xtrabackup --decrypt=AES256 --encrypt-key-file=... --target-dir=...` to decrypt the `.xbcrypt` files in-place
2. Runs `xtrabackup --prepare --target-dir=...` to apply the redo log and make the backup consistent

This is a two-step process because xtrabackup cannot apply the redo log to encrypted files directly. Both steps happen automatically — you do not need to do anything manually.
