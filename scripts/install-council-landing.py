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
            stage.chmod(0o755)  # Nginx must traverse the installed release directory.
            with tarfile.open(archive, "r:gz") as tar:
                members = tar.getmembers()
                if len(members) > 100 or sum(m.size for m in members) > 20 * 1024 * 1024:
                    raise SystemExit("Artifact too large")
                for member in members:
                    parts = Path(member.name).parts
                    if (
                        not member.isfile()
                        or Path(member.name).is_absolute()
                        or not parts
                        or any(p in ("..", ".") for p in parts)
                    ):
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
    # Validate the candidate before changing the live pointer. A broken
    # archive must never replace a working landing.
    for name in ("index.html", "en/index.html"):
        page = (target / name).read_text(encoding="utf-8")
        if "council.zavarov.com" not in page or "<html" not in page:
            raise SystemExit(f"Invalid Council page: {name}")
    if LINK.is_symlink():
        current = LINK.resolve(strict=True)
        if not current.is_relative_to(RELEASES.resolve(strict=True)):
            raise SystemExit("Live landing points outside managed releases")
    elif LINK.exists():
        raise SystemExit("Refusing to replace non-symlink live landing")
    previous = LINK.resolve(strict=True) if LINK.is_symlink() else None
    temp_link = DEST / f".agent-council-landing-{os.getpid()}"
    try:
        temp_link.symlink_to(target)
        os.replace(temp_link, LINK)
        # Verify the switched pointer, not just the candidate on disk.
        if LINK.resolve(strict=True) != target.resolve(strict=True):
            raise RuntimeError("Static landing pointer verification failed")
        for name in ("index.html", "en/index.html"):
            if not (LINK / name).is_file():
                raise RuntimeError(f"Missing live page: {name}")
    except Exception:
        if previous is not None:
            rollback_link = DEST / f".agent-council-rollback-{os.getpid()}"
            rollback_link.symlink_to(previous)
            os.replace(rollback_link, LINK)
        elif LINK.is_symlink():
            LINK.unlink()
        raise
    finally:
        temp_link.unlink(missing_ok=True)
    print(f"Installed static Council landing {actual}; HTTP checks still required")


if __name__ == "__main__":
    main()
