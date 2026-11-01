"""One metadata contract for reviewed skills and the isolated skill reader."""

import re

NAME = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
MAX_BYTES = 65536


def load_yaml(header):
    # Use the pinned Hermes parser, including its safe YAML loader. This adapter
    # is exercised inside the real image; local filesystem tests need no PyYAML.
    from utils import fast_safe_load

    try:
        return fast_safe_load(header)
    except Exception:
        raise ValueError("Invalid skill YAML metadata.") from None


def metadata(content, expected_name):
    if not isinstance(content, str) or len(content.encode("utf-8")) > MAX_BYTES:
        raise ValueError("Skill text exceeds its byte limit.")
    normalized = content.replace("\r\n", "\n")
    header, separator, body = normalized[4:].partition("\n---\n")
    if not normalized.startswith("---\n") or not separator or not body.strip():
        raise ValueError("Skills need YAML front matter and a Markdown body.")
    value = load_yaml(header)
    if not isinstance(value, dict):
        raise ValueError("Skill metadata must be a mapping.")
    name, description = value.get("name"), value.get("description")
    if (
        not isinstance(name, str)
        or len(name) > 64
        or not NAME.fullmatch(name)
        or name != expected_name
    ):
        raise ValueError("Skill name must match its installed directory.")
    if not isinstance(description, str) or not 1 <= len(description.strip()) <= 1024:
        raise ValueError("Skills need a description of at most 1024 characters.")
    return {"name": name, "description": description.strip()}
