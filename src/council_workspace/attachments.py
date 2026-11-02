"""Bounded access to inbound files, never to profile configuration or credentials."""

import hashlib
import os
import stat
from pathlib import Path

MAX_BYTES = 50 * 1024 * 1024
KINDS = {"images", "documents", "audio", "videos"}


def snapshot(home, raw):
    if not isinstance(raw, str):
        raise ValueError("Use the local attachment path supplied with the message.")
    path = Path(raw)
    if not path.is_absolute():
        path = home / path
    try:
        relative = path.relative_to(home / "cache")
    except ValueError:
        raise PermissionError(
            "Only inbound attachments in this profile's cache may be uploaded."
        ) from None
    if not relative.parts or relative.parts[0] not in KINDS or path.resolve() != path:
        raise PermissionError("The attachment must be a regular file inside its original cache.")
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), "rb") as source:
        before = os.fstat(source.fileno())
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or not 0 < before.st_size <= MAX_BYTES
        ):
            raise ValueError("Use a nonempty regular attachment of at most 50 MiB.")
        data = source.read(MAX_BYTES + 1)
        after = os.fstat(source.fileno())
    if len(data) != before.st_size or (before.st_mtime_ns, before.st_ctime_ns) != (
        after.st_mtime_ns,
        after.st_ctime_ns,
    ):
        raise ValueError("The attachment changed while reading; inspect it again.")
    return data, hashlib.sha256(data).hexdigest()
