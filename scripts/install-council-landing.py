#!/usr/bin/env python3
"""Install one bounded, reviewed static artifact under the Council operations lock."""

import argparse
import contextlib
import fcntl
import gzip
import hashlib
import io
import os
import re
import stat
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path, PurePosixPath

DEST = Path("/var/www")
LINK = DEST / "agent-council-landing"
RELEASES = DEST / "agent-council-landing-releases"
LOCK = Path("/etc/agent-council/operations.lock")
MAX_ARCHIVE = 8 * 1024 * 1024
MAX_CONTENT = 20 * 1024 * 1024
MAX_TAR = 24 * 1024 * 1024


def trusted_directory(path):
    info = path.lstat()
    if (
        path.resolve() != path
        or not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.geteuid()
        or info.st_mode & 0o022
    ):
        raise ValueError("Expected an operator-owned directory without symlinks")


@contextlib.contextmanager
def operation_lock():
    trusted_directory(LOCK.parent)
    fd = os.open(LOCK, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.geteuid()
            or info.st_nlink != 1
            or info.st_mode & 0o022
        ):
            raise ValueError("Unsafe Council operations lock")
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(fd)


def read_artifact(path, expected):
    if not re.fullmatch(r"[a-f0-9]{64}", expected):
        raise ValueError("Expected a lowercase SHA-256")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as source:
        info = os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_ARCHIVE:
            raise ValueError("Expected a bounded regular archive")
        data = source.read(MAX_ARCHIVE + 1)
    if len(data) > MAX_ARCHIVE or hashlib.sha256(data).hexdigest() != expected:
        raise ValueError("Artifact SHA-256 mismatch or size limit exceeded")
    # Parse exactly the bytes checked above, even if the unprivileged sender
    # replaces its archive after this point. Bound decompression before tar parsing.
    with gzip.GzipFile(fileobj=io.BytesIO(data)) as compressed:
        expanded = compressed.read(MAX_TAR + 1)
    if len(expanded) > MAX_TAR:
        raise ValueError("Expanded archive exceeds size limit")
    files = {}
    size = 0
    with tarfile.open(fileobj=io.BytesIO(expanded), mode="r:") as archive:
        for entry in archive:
            name = entry.name
            path = PurePosixPath(name)
            size += entry.size
            if (
                not entry.isfile()
                or path.is_absolute()
                or str(path) != name
                or any(part in ("..", ".") for part in path.parts)
                or not (name in ("index.html", "en/index.html") or name.startswith("council/"))
                or name in files
                or len(files) >= 100
                or entry.size < 0
                or size > MAX_CONTENT
            ):
                raise ValueError("Unsafe or oversized archive entry")
            with archive.extractfile(entry) as source:
                files[name] = source.read()
    for required in ("index.html", "en/index.html", "council/avatars/qui-gon.png"):
        if required not in files:
            raise ValueError(f"Missing {required}")
    for name in ("index.html", "en/index.html"):
        page = files[name].decode("utf-8")
        if "council.zavarov.com" not in page or "<html" not in page:
            raise ValueError(f"Invalid Council page: {name}")
    return files


def verify_release(target, files):
    trusted_directory(target)
    actual = set()
    for path in target.rglob("*"):
        info = path.lstat()
        if info.st_uid != os.geteuid() or info.st_mode & 0o022 or path.is_symlink():
            raise ValueError("Unsafe cached release entry")
        if path.is_dir():
            continue
        name = path.relative_to(target).as_posix()
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or name not in files
            or info.st_size != len(files[name])
            or path.read_bytes() != files[name]
        ):
            raise ValueError("Cached release differs from reviewed artifact")
        actual.add(name)
    if actual != set(files):
        raise ValueError("Cached release is incomplete")


def install(files, digest, verify_url):
    trusted_directory(DEST)
    RELEASES.mkdir(mode=0o755, exist_ok=True)
    trusted_directory(RELEASES)
    target = RELEASES / digest
    if target.exists() or target.is_symlink():
        verify_release(target, files)
    else:
        with tempfile.TemporaryDirectory(prefix=".stage-", dir=RELEASES) as staging:
            stage = Path(staging)
            stage.chmod(0o755)
            for name, data in files.items():
                path = stage / name
                path.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
                path.write_bytes(data)
                path.chmod(0o644)
            verify_release(stage, files)
            os.rename(stage, target)
    previous = LINK.resolve(strict=True) if LINK.is_symlink() else None
    if previous is not None:
        if previous.parent != RELEASES:
            raise ValueError("Live landing points outside managed releases")
        trusted_directory(previous)
    elif LINK.exists():
        raise ValueError("Refusing to replace non-symlink live landing")
    temp_link = DEST / f".agent-council-landing-{os.getpid()}"
    try:
        temp_link.symlink_to(target)
        os.replace(temp_link, LINK)
        if LINK.resolve(strict=True) != target:
            raise RuntimeError("Static landing pointer verification failed")
        verify_release(LINK.resolve(strict=True), files)
        if verify_url:
            subprocess.run(
                [
                    sys.executable,
                    str(Path(__file__).with_name("verify-council-landing.py")),
                    "--base-url",
                    verify_url,
                    "--origin-address",
                    "127.0.0.1",
                    "--ru-sha256",
                    hashlib.sha256(files["index.html"]).hexdigest(),
                    "--en-sha256",
                    hashlib.sha256(files["en/index.html"]).hexdigest(),
                ],
                check=True,
                timeout=30,
            )
    except Exception:
        if previous is not None:
            temp_link.unlink(missing_ok=True)
            temp_link.symlink_to(previous)
            os.replace(temp_link, LINK)
        elif LINK.is_symlink():
            LINK.unlink()
        raise
    finally:
        temp_link.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("archive", type=Path)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--verify-url", choices=["https://council.zavarov.com"])
    args = parser.parse_args()
    try:
        files = read_artifact(args.archive, args.sha256)
        with operation_lock():
            install(files, args.sha256, args.verify_url)
    except (OSError, ValueError, tarfile.TarError) as error:
        raise SystemExit(str(error)) from None
    print(f"Installed static Council landing {args.sha256}")


if __name__ == "__main__":
    main()
