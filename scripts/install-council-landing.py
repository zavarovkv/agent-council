#!/usr/bin/env python3
"""Operator-only install of a reviewed Hugo artifact, no network or credentials."""

import argparse
import hashlib
import os
import tarfile
import tempfile
from pathlib import Path

DEST = Path("/var/www")
LINK = DEST / "agent-council-landing"
RELEASES = DEST / "agent-council-landing-releases"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("archive", type=Path)
    parser.add_argument("--sha256", required=True)
    args = parser.parse_args()
    archive = args.archive.resolve(strict=True)
    actual = hashlib.sha256(archive.read_bytes()).hexdigest()
    if actual != args.sha256.lower():
        raise SystemExit("Artifact SHA-256 mismatch")
    RELEASES.mkdir(mode=0o755, parents=True, exist_ok=True)
    target = RELEASES / actual[:16]
    if not target.exists():
        with tempfile.TemporaryDirectory(prefix=".stage-", dir=RELEASES) as staging:
            stage = Path(staging)
            with tarfile.open(archive, "r:gz") as tar:
                members = tar.getmembers()
                if len(members) > 100 or sum(m.size for m in members) > 20 * 1024 * 1024:
                    raise SystemExit("Artifact too large")
                for member in members:
                    parts = Path(member.name).parts
                    if not member.isfile() or not parts or any(p in ("..", ".") for p in parts):
                        raise SystemExit("Unsafe archive entry")
                    out = stage.joinpath(*parts)
                    out.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
                    with tar.extractfile(member) as source, out.open("wb") as dest:
                        dest.write(source.read())
                    out.chmod(0o644)
            for required in ("index.html", "en/index.html", "council/avatars/qui-gon.png"):
                if not (stage / required).is_file():
                    raise SystemExit(f"Missing {required}")
            os.rename(stage, target)
    temp_link = DEST / f".agent-council-landing-{os.getpid()}"
    temp_link.symlink_to(target)
    os.replace(temp_link, LINK)
    print(f"Installed static Council landing {actual}; verify Nginx separately")


if __name__ == "__main__":
    main()
