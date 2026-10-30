"""Encrypted R2 storage through the official pinned maintenance-only restic image."""

import json
import os
import re
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

from .config import OperationError, atomic_write, read_settings
from .lifecycle import smoke
from .snapshots import load_manifest, snapshot

SNAPSHOT_ID = re.compile(r"[0-9a-f]{64}\Z")
TAG = "application:agent-council"


def validate_r2(values):
    if set(values) != {
        "RESTIC_REPOSITORY",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_DEFAULT_REGION",
    }:
        raise OperationError("Use the documented R2 backup environment keys only.")
    repository = values["RESTIC_REPOSITORY"]
    if not repository.startswith("s3:https://"):
        raise OperationError("The off-host repository must use the R2 HTTPS S3 endpoint.")
    origin = urlsplit(repository.removeprefix("s3:"))
    if (
        not origin.hostname
        or not re.fullmatch(
            r"[a-f0-9]{32}(?:\.eu|\.fedramp)?\.r2\.cloudflarestorage\.com", origin.hostname
        )
        or origin.username
        or origin.password
        or origin.query
        or origin.fragment
        or origin.port not in (None, 443)
        or not re.fullmatch(r"/[a-z0-9][a-z0-9.-]{1,61}[a-z0-9](?:/[A-Za-z0-9/_-]+)?", origin.path)
        or ".." in origin.path.split("/")
    ):
        raise OperationError("Use the exact R2 account endpoint and a dedicated bucket prefix.")
    if values["AWS_DEFAULT_REGION"] != "auto":
        raise OperationError("R2 uses AWS_DEFAULT_REGION=auto.")
    for key in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"):
        if not re.fullmatch(r"[A-Za-z0-9/+=_-]{16,128}", values[key]):
            raise OperationError("R2 credentials are missing or malformed.")


class Restic:
    def __init__(self, runtime):
        self.runtime = runtime
        self.settings = runtime.settings
        self.env_file = self.settings.env_file.with_name("backup.env")
        self.password_file = self.settings.env_file.with_name("backup-password")
        validate_r2(read_settings(self.env_file))
        for path in (self.env_file, self.password_file):
            if path.is_symlink() or not path.is_file():
                raise OperationError("Regular private R2 configuration files are required.")
            metadata = path.stat()
            if metadata.st_uid != os.geteuid() or metadata.st_mode & 0o077:
                raise OperationError("Backup configuration must be root-owned and mode 0600.")
        if self.password_file.stat().st_size < 24:
            raise OperationError(
                "Use a strong restic password and retain a separate recovery copy."
            )

    def run(self, arguments, *, source, target):
        environment = dict(os.environ)
        environment.update(
            COUNCIL_BACKUP_ENV_FILE=str(self.env_file),
            COUNCIL_BACKUP_PASSWORD_FILE=str(self.password_file),
            COUNCIL_SNAPSHOT_DIR=str(source),
            COUNCIL_RESTORE_DIR=str(target),
        )
        return self.runtime.run(
            [
                "docker",
                "compose",
                "--project-name",
                "agent-council-backup",
                "-f",
                str(self.settings.repository / "compose.backup.yaml"),
                "run",
                "--rm",
                "--no-deps",
                "restic",
                *arguments,
            ],
            timeout=3600,
            environment=environment,
        )

    def initialize(self):
        self.settings.backup_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix=".restic-init-", dir=self.settings.backup_dir
        ) as temp:
            self.run(["init"], source=Path(temp), target=Path(temp))

    def upload(self, backup):
        backup, _ = load_manifest(backup)
        with tempfile.TemporaryDirectory(
            prefix=".restic-upload-", dir=self.settings.backup_dir
        ) as temp:
            output = self.run(
                [
                    "backup",
                    "--json",
                    "--host",
                    "agent-council",
                    "--tag",
                    TAG,
                    "--",
                    "/snapshot",
                ],
                source=backup,
                target=Path(temp),
            )
        summaries = [json.loads(line) for line in output.splitlines() if line.startswith("{")]
        identifiers = [
            entry.get("snapshot_id", "")
            for entry in summaries
            if entry.get("message_type") == "summary"
        ]
        if len(identifiers) != 1 or not SNAPSHOT_ID.fullmatch(identifiers[0]):
            raise OperationError("Restic did not return a complete snapshot ID.")
        return identifiers[0]

    def download(self, identifier, target):
        if not SNAPSHOT_ID.fullmatch(identifier):
            raise OperationError("Use the full restic snapshot ID.")
        if target.is_symlink() or any(target.iterdir()):
            raise OperationError("Cloud recovery must target an empty private staging directory.")
        self.run(["restore", identifier, "--target", "/restore"], source=target, target=target)
        restored = target / "snapshot"
        load_manifest(restored)
        return restored

    def check(self, *, full=False):
        with tempfile.TemporaryDirectory(
            prefix=".restic-check-", dir=self.settings.backup_dir
        ) as temp:
            args = ["check", "--read-data"] if full else ["check"]
            self.run(args, source=Path(temp), target=Path(temp))

    def retain(self):
        with tempfile.TemporaryDirectory(
            prefix=".restic-retain-", dir=self.settings.backup_dir
        ) as temp:
            self.run(
                [
                    "forget",
                    "--host",
                    "agent-council",
                    "--tag",
                    TAG,
                    "--keep-daily",
                    "7",
                    "--keep-weekly",
                    "4",
                    "--keep-monthly",
                    "6",
                    "--prune",
                ],
                source=Path(temp),
                target=Path(temp),
            )


def cloud_backup(runtime):
    remote = Restic(runtime)  # Validate credentials before stopping the application.
    backup = snapshot(runtime, portable=True)
    identifier = remote.upload(backup)
    remote.check()
    with tempfile.TemporaryDirectory(
        prefix=".cloud-restore-check-", dir=runtime.settings.backup_dir
    ) as temp:
        restored = remote.download(identifier, Path(temp))
        smoke(runtime, restored)
    # Only apply retention after a real download and offline profile verification.
    remote.retain()
    _, manifest = load_manifest(backup)
    manifest["offsite_verified"] = identifier
    atomic_write(backup / "manifest.json", (json.dumps(manifest, indent=2) + "\n").encode())
    prune_local(runtime.settings.backup_dir, keep=7)
    atomic_write(
        runtime.settings.env_file.with_name("last-cloud-backup.json"),
        (
            json.dumps({"snapshot": identifier, "verified_at": datetime.now(UTC).isoformat()})
            + "\n"
        ).encode(),
    )
    return {"backup": str(backup), "cloud_snapshot": identifier, "recovery_verified": True}


def prune_local(root, *, keep):
    """Prune only this tool's complete, verified-offsite restore points."""
    if keep < 1:
        raise OperationError("Keep at least one verified local restore point.")
    verified = []
    for path in root.glob("backup-*"):
        if path.is_symlink() or not path.is_dir():
            continue
        manifest_path = path / "manifest.json"
        if manifest_path.is_symlink() or not manifest_path.is_file():
            continue
        try:
            manifest = json.loads(manifest_path.read_text())
        except (ValueError, OSError):
            continue
        if (
            manifest.get("application") == "agent-council"
            and manifest.get("schema") == 1
            and SNAPSHOT_ID.fullmatch(str(manifest.get("offsite_verified", "")))
        ):
            verified.append(path)
    for path in sorted(verified, reverse=True)[keep:]:
        shutil.rmtree(path)
