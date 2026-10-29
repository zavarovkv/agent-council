#!/usr/bin/env python3
"""Convert the previous quoted env file before enabling Compose raw format."""

import os
from pathlib import Path

from env_utils import parse_environment, write_environment


def main():
    if os.geteuid() != 0:
        raise SystemExit("Run on the deployment server as root.")
    target = Path("/etc/hermes/telegram.env")
    marker = target.with_suffix(".env.format")
    if marker.exists() and marker.read_text(encoding="utf-8").strip() == "raw":
        print("Authentication environment is already raw.")
        return
    backup = target.with_suffix(".env.before-raw")
    if backup.exists():
        raise SystemExit("A previous migration backup exists; inspect it before retrying.")
    original = target.read_text(encoding="utf-8")
    values = parse_environment(original, raw=False)
    descriptor = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as output:
        output.write(original)
        output.flush()
        os.fsync(output.fileno())
    write_environment(target, values, raw=True)
    marker.write_text("raw\n", encoding="utf-8")
    marker.chmod(0o600)
    print("Authentication environment converted; the original is backed up privately.")


if __name__ == "__main__":
    main()
