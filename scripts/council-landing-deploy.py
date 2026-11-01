#!/usr/bin/env python3
"""Restricted SSH forced command for a reviewed static Council release.

Run only through a dedicated unprivileged SSH account with a narrowly scoped
sudoers rule for the trusted installer; no arbitrary remote command execution.
"""

import hashlib
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

MAX_ARCHIVE = 8 * 1024 * 1024
INSTALLER = "/usr/local/libexec/council/install-council-landing.py"


def install_archive(archive, expected):
    command = [
        "sudo",
        "-n",
        INSTALLER,
        str(archive),
        "--sha256",
        expected,
        "--verify-url",
        "https://council.zavarov.com",
    ]
    for delay in (5, 10, 20, 30, None):
        result = subprocess.run(command, check=False, timeout=90)
        if result.returncode == 0:
            return
        if result.returncode != 75 or delay is None:
            raise subprocess.CalledProcessError(result.returncode, command)
        time.sleep(delay)


def main():
    requested = os.environ.get("SSH_ORIGINAL_COMMAND", "")
    match = re.fullmatch(r"council-landing-deploy ([0-9a-f]{64})", requested)
    if not match:
        raise SystemExit("Unauthorized deployment command")
    expected = match.group(1)
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
        install_archive(archive, expected)
    print("Council landing installed and origin verified")


if __name__ == "__main__":
    main()
