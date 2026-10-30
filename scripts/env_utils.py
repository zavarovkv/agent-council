"""Read and atomically write the small, root-owned authentication env file."""

import fcntl
import json
import os
import re
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Mapping

OPERATIONS_LOCK = Path("/etc/agent-council/operations.lock")


@contextmanager
def operation_lock(path=OPERATIONS_LOCK):
    """Serialize credential/access changes with deployments and consistent backups."""
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    flags = os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW
    with os.fdopen(os.open(path, flags, 0o600), "a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit(
                "Another deployment, backup or account setup is running; retry later."
            ) from None
        yield


def parse_environment(text: str, *, raw: bool) -> dict:
    values = {}
    for line in text.split("\n"):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if not separator or not re.fullmatch(r"[A-Z][A-Z0-9_]*", key):
            raise ValueError("Invalid authentication environment file.")
        if not raw and value.startswith('"'):
            value = json.loads(value)
            if not isinstance(value, str):
                raise ValueError("Invalid environment value.")
        values[key] = value
    return values


def render_environment(values: Mapping[str, str], *, raw: bool) -> str:
    lines = []
    for key, value in values.items():
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*", key):
            raise ValueError("Invalid environment key.")
        if any(ord(character) < 32 for character in value):
            raise ValueError("Environment values cannot contain control characters.")
        encoded = value if raw else json.dumps(value, ensure_ascii=False)
        lines.append(f"{key}={encoded}\n")
    return "".join(lines)


def write_environment(path: Path, values: Mapping[str, str], *, raw: bool) -> None:
    if path.is_symlink():
        raise ValueError("Refusing a symlinked authentication file.")
    text = render_environment(values, raw=raw)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, prefix=".telegram-", delete=False
        ) as output:
            temporary = Path(output.name)
            output.write(text)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
