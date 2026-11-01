#!/usr/bin/env python3
"""Restricted SSH forced command for a reviewed static Council release.

Run only through a dedicated unprivileged SSH account with a narrowly scoped
sudoers rule for the trusted installer; no arbitrary remote command execution.
"""

import fcntl
import hashlib
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

MAX_ARCHIVE = 8 * 1024 * 1024
INSTALLER = "/usr/local/libexec/council/install-council-landing.py"
LOCK = "/tmp/council-landing-deploy.lock"


def main():
    requested = os.environ.get("SSH_ORIGINAL_COMMAND", "")
    match = re.fullmatch(r"council-landing-deploy ([0-9a-f]{64})", requested)
    if not match:
        raise SystemExit("Unauthorized deployment command")
    expected = match.group(1)
    with open(LOCK, "w", encoding="utf-8") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with tempfile.TemporaryDirectory(prefix="council-landing-") as tmp:
            archive = Path(tmp) / "release.tar.gz"
            digest = hashlib.sha256()
            size = 0
            with archive.open("wb") as out:
                while True:
                    chunk = sys.stdin.buffer.read(65536)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > MAX_ARCHIVE:
                        raise SystemExit("Archive exceeds size limit")
                    digest.update(chunk)
                    out.write(chunk)
            if size == 0 or digest.hexdigest() != expected:
                raise SystemExit("Archive checksum mismatch")
            # Privileged installer is a fixed reviewed file. No user-controlled
            # shell, path or environment interpolation.
            subprocess.run(
                [
                    "sudo", "-n", INSTALLER, str(archive), "--sha256", expected,
                    "--verify-url", "https://council.zavarov.com",
                ],
                check=True,
                timeout=90,
            )
    print("Council landing archive installed; routing verification required")


if __name__ == "__main__":
    main()
