# Backup and recovery

Git stores code. Private restore points store mutable application state. R2 is
an off-host object store; restic encrypts data and metadata before upload.
A second directory on the same disk does not protect against losing the server.

## Scope

A restore point contains the Hermes profile (sessions, memories, model/OAuth
connections, skills, agent profiles and downloaded files), `/workspace`, the
root-only Telegram authentication environment, optional secretary service key
and the active Compose source. The secretary's own profile/history are included.
Cloud backups contain **data only**: Docker images, installed tools, dependency
environments, caches, logs, telemetry and nested old backups are excluded. The
excluded entries are recorded in the snapshot manifest and in `DISPOSABLE` in
`src/council_ops/snapshots.py`. The same exclusions apply at child-profile roots;
workspace content and custom skill source are never filtered. Container images
are rebuilt from the recorded Git revision and digest-pinned Dockerfile.
Local upgrade checkpoints retain the full profile for immediate rollback;
`backup --include-image` optionally adds an image tar for local offline recovery.
Files are checksummed, symlinks are preserved without following their targets,
and ephemeral sockets are excluded. External symlink targets are not backed up.

This does not back up the host OS, TravelLine, remote Notion pages or all Google
Drive content. Saved connections do not make copies of remote files. Nginx/TLS
must be provisioned separately on a replacement host. Revoked or expired
provider credentials still require reauthorization.

## R2 setup

1. Create a dedicated private R2 bucket, for example `agent-council-backups`.
   Leave public access disabled. Create an **Object Read & Write** token restricted
   to that bucket; use the account S3 endpoint shown by Cloudflare.
2. Create a strong recovery password in your password manager and keep it
   independently of this server. Losing it makes encrypted backups unrecoverable.
3. From your local checkout, run `bash scripts/setup-backups.sh`. Enter the
   repository endpoint/bucket prefix, Access Key ID, Secret Access Key and recovery
   password in the SSH terminal. Hidden prompts save root-only files outside Git.
   Do not put secrets in chat, arguments, Actions secrets or an agent profile.
4. On the server, initialize a **new** repository once, then perform its first
   backup and recovery check:

   ```bash
   cd /opt/agent-council
   sudo python3 scripts/manage.py init-cloud-backups
   sudo python3 scripts/manage.py cloud-backup
   ```

For an existing repository, supply its original password and skip initialization.
The setup command retains an existing password; intentional password rotation
uses restic key management. R2 token rotation changes only storage credentials.
The repository setting has this form, using the exact account endpoint:

```text
s3:https://ACCOUNT_ID.r2.cloudflarestorage.com/BUCKET/production
```

After the first backup reports `recovery_verified: true`, enable scheduling:

```bash
sudo install -m 0644 config/agent-council-backup.service config/agent-council-backup.timer /etc/systemd/system/
sudo systemd-analyze verify /etc/systemd/system/agent-council-backup.service /etc/systemd/system/agent-council-backup.timer
sudo systemctl daemon-reload
sudo systemctl enable --now agent-council-backup.timer
systemctl list-timers agent-council-backup.timer
```

The timer runs daily at 01:00 UTC, with up to 15 minutes of jitter, and catches
missed runs. It retries failures, including a busy profile. Check
`systemctl status agent-council-backup.service` and
`journalctl -u agent-council-backup.service`. No external alert destination is
configured; connect failures to your monitoring before relying on unattended
backups. GitHub Actions receives no deployment or storage credentials.

## Consistency and retention

Hermes and its optional secretary stop while profiles/workspace are copied. A known
active agent/model/OAuth setup blocks maintenance. Use a quiet window: there is
brief dashboard/Telegram downtime, and a message racing the idle check can be
interrupted by graceful shutdown. A distributed drain/queue is not implemented.
Hashing, uploads and recovery checks happen after Hermes restarts. Exclusions
change the copied checkpoint only; they never delete anything from the live profile.

The maintenance restic container has no Docker socket, published ports or access
to unrelated services. Its keys never reach the agent. Every cloud run uploads,
checks repository structure, downloads the new snapshot and opens copied session
databases in its recorded image without network access. Only then does retention
keep **7 daily, 4 weekly and 6 monthly** snapshots. Restic deduplicates and compresses
data. Allow disk space for a local checkpoint and verification copy.

The most recent 7 verified local checkpoints remain. Manual, unverified and
foreign backups are never pruned. Failed runs exit nonzero and retain the local
checkpoint. Periodically verify all encrypted data as well:

```bash
sudo python3 scripts/manage.py check-cloud-backups --read-data
```

Do not add an R2 expiry lifecycle or a whole-bucket lock to the restic repository:
restic must delete temporary locks and manage its own retained packs. Separate
immutable copies need a separately designed/tested policy. A writer key can
delete backups; keep it root-only and bucket-scoped. Encryption does not protect
against an administrator deleting the bucket.

## Local rollback

Run on the Docker host; the first command prints its private restore-point path:

```bash
sudo python3 scripts/manage.py backup --include-image
sudo python3 scripts/manage.py verify-backup /var/backups/agent-council/backup-TIMESTAMP-ID
sudo python3 scripts/manage.py restore /var/backups/agent-council/backup-TIMESTAMP-ID --confirm
```

Manual restore disables the optional instruction-update timer so it cannot
immediately undo restored rules. Re-enable that timer after reviewing the intended revision.

Restore checks a copy, stops only Hermes and swaps its data/workspace. Existing
directories are retained as `*.before-restore-*`; a failed restored boot puts
them back. Authentication and Compose are restored with the image, while this
host's paths, UID/GID and HTTPS origin remain configured locally. Restoring
rewinds application history to the checkpoint.

## Losing the server

On a clean Linux host, install Docker/Python, the dedicated `hermes` account and
the reviewed Git checkout. Create `/etc/agent-council/compose.env` from the example
with this host's paths/UID/GID/origin, mode 0600. Do **not** start an empty profile
with `install`. Recover R2 credentials and the original password using
`configure-backups`; use the existing repository, not `init`.

```bash
sudo python3 scripts/manage.py download-backup FULL_64_CHARACTER_SNAPSHOT_ID
sudo python3 scripts/manage.py recover /var/backups/agent-council/cloud-recovery-ID/snapshot --rebuild --confirm
```

Download verifies file checksums without changing live state. Read the manifest's
`revision` and check out that exact Git revision before `recover --rebuild`.
`recover` requires absent/empty data directories and no existing Hermes container.
It rebuilds the recorded release, checks copied session databases offline and
starts the restored profile. Network access to pinned image/package sources is
required to rebuild software; upstream tool environments regenerate on demand.
For a retained local image or optional image tar, omit `--rebuild`. The downloaded
checkpoint remains available if boot fails. Recreate only Council's HTTPS route,
check origin authorization and verify a real Telegram/model round trip before
reopening traffic. Retain the original snapshot and independent password until
a separate replacement-host exercise succeeds.

See [Cloudflare R2 setup](https://developers.cloudflare.com/r2/get-started/s3/) and
[restic repository preparation](https://restic.readthedocs.io/en/stable/030_preparing_a_new_repo.html).
