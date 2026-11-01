"""Private, read-only CouncilBase snapshot validation and cache helpers.

This module deliberately does not authenticate to Drive or expose data to agents.
The trusted owner runtime must supply bytes fetched through its existing grant.
"""

import hashlib
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path

MAX_BYTES = 256 * 1024
DOCUMENT_ID = re.compile(r"^[A-Za-z0-9_-]{20,128}$")


class ContextValidationError(ValueError):
    """Invalid remote context or private cache configuration."""


def validate_document(document_id: str, content: bytes, mime_type: str) -> str:
    if not DOCUMENT_ID.fullmatch(document_id):
        raise ContextValidationError("Invalid configured document ID")
    if mime_type not in ("text/markdown", "text/plain"):
        raise ContextValidationError("Unexpected context MIME type")
    if not content or len(content) > MAX_BYTES or b"\x00" in content:
        raise ContextValidationError("Context must be nonempty, bounded UTF-8 text")
    try:
        text = content.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise ContextValidationError("Context must be UTF-8") from exc
    if not text.strip():
        raise ContextValidationError("Empty context")
    return text


def write_snapshot(directory: Path, document_id: str, content: bytes, mime_type: str):
    """Atomically store a validated snapshot in an existing private directory."""
    text = validate_document(document_id, content, mime_type)
    directory = Path(directory)
    if directory.is_symlink() or not directory.is_dir():
        raise ContextValidationError("Private cache directory must already exist")
    if directory.stat().st_mode & 0o077:
        raise ContextValidationError("Private cache directory is not owner-only")
    payload = {
        "document_id": document_id,
        "synced_at": datetime.now(timezone.utc).isoformat(),
        "sha256": hashlib.sha256(content).hexdigest(),
        "text": text,
    }
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=directory, prefix=".context-", delete=False
        ) as file:
            temp_path = Path(file.name)
            os.fchmod(file.fileno(), 0o600)
            json.dump(payload, file, ensure_ascii=False)
            file.flush()
            os.fsync(file.fileno())
        target = directory / "councilbase-context.json"
        if target.is_symlink():
            raise ContextValidationError("Refusing symlinked cache target")
        os.replace(temp_path, target)
        temp_path = None
        return payload["sha256"]
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def read_snapshot(directory: Path, document_id: str):
    """Read only the configured document; remote text is data, not instructions."""
    path = Path(directory) / "councilbase-context.json"
    if path.is_symlink() or not path.is_file():
        raise ContextValidationError("No safe cached context")
    if path.stat().st_mode & 0o077 or path.stat().st_size > MAX_BYTES * 2:
        raise ContextValidationError("Unsafe cached context permissions or size")
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("document_id") != document_id:
        raise ContextValidationError("Cache belongs to a different document")
    content = data["text"].encode("utf-8")
    if hashlib.sha256(content).hexdigest() != data.get("sha256"):
        raise ContextValidationError("Cache checksum mismatch")
    validate_document(document_id, content, "text/markdown")
    return data
