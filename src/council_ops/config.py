"""Validate operator-owned settings without evaluating shell or Compose syntax."""

import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit


class OperationError(RuntimeError):
    """Safe operator-facing error; never include subprocess output or credentials."""


def read_settings(path):
    if path.is_symlink() or not path.is_file():
        raise OperationError("A regular deployment env file is required.")
    values = {}
    for line in path.read_text().splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if (
            not separator
            or not re.fullmatch(r"[A-Z][A-Z0-9_]*", key)
            or key in values
            or value != value.strip()
            or any(character in value for character in "\"'`$\x00")
        ):
            raise OperationError("Use unique KEY=value lines with literal, unquoted values.")
        values[key] = value
    return values


def atomic_write(path, data, *, mode=0o600):
    if path.is_symlink():
        raise OperationError("Refusing a symlinked configuration file.")
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".council-", delete=False) as out:
            temporary = Path(out.name)
            os.fchmod(out.fileno(), mode)
            out.write(data)
            out.flush()
            os.fsync(out.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def contained(path, parent):
    return path == parent or parent in path.parents


@dataclass(frozen=True)
class Settings:
    repository: Path
    env_file: Path
    backup_dir: Path
    values: dict

    @classmethod
    def load(cls, repository, env_file, backup_dir):
        repository = Path(repository).resolve(strict=True)
        env_file = Path(env_file).absolute()
        values = read_settings(env_file)
        backup_dir = Path(backup_dir).absolute()
        settings = cls(repository, env_file, backup_dir, values)
        if env_file == settings.auth_file:
            raise OperationError("Deployment settings and authentication must use separate files.")
        locations = [settings.data_dir, settings.workspace_dir, backup_dir]
        for path in [*locations, env_file, settings.auth_file]:
            if not path.is_absolute() or path.resolve() != path:
                raise OperationError("Use absolute paths without symlinks or parent traversal.")
            if any(character in str(path) for character in ",:\n\r"):
                raise OperationError("Deployment paths must not contain mount delimiters.")
            if contained(path, repository):
                raise OperationError("State, secrets and backups must stay outside the checkout.")
        for index, path in enumerate(locations):
            if len(path.parts) < 4:
                raise OperationError("Use a dedicated deployment subdirectory.")
            for other in locations[index + 1 :]:
                if contained(path, other) or contained(other, path):
                    raise OperationError("Data, workspace and backup directories must not overlap.")
            for config in (env_file, settings.auth_file):
                if contained(config, path):
                    raise OperationError("Host configuration must be outside the runtime volumes.")
        for name in ("HERMES_UID", "HERMES_GID"):
            if (
                not re.fullmatch(r"[0-9]+", values.get(name, ""))
                or not 0 < int(values[name]) < 2**31
            ):
                raise OperationError("Explicit non-root HERMES_UID and HERMES_GID are required.")
        url = urlsplit(values.get("HERMES_PUBLIC_URL", ""))
        if (
            url.scheme != "https"
            or not url.hostname
            or url.username
            or url.password
            or url.query
            or url.fragment
            or url.path not in ("", "/")
        ):
            raise OperationError("HERMES_PUBLIC_URL must be an HTTPS origin.")
        port = values.get("HERMES_HTTP_PORT", "9119")
        if not port.isdigit() or not 1024 <= int(port) <= 65535:
            raise OperationError("HERMES_HTTP_PORT must be between 1024 and 65535.")
        if not values.get("COUNCIL_HERMES_IMAGE"):
            raise OperationError("Set COUNCIL_HERMES_IMAGE explicitly.")
        return settings

    def required_path(self, key):
        value = self.values.get(key, "")
        if not value:
            raise OperationError(f"Set {key} explicitly in the deployment env file.")
        return Path(value)

    @property
    def data_dir(self):
        return self.required_path("HERMES_DATA_DIR")

    @property
    def workspace_dir(self):
        return self.required_path("HERMES_WORKSPACE_DIR")

    @property
    def auth_file(self):
        return self.required_path("HERMES_AUTH_ENV_FILE")

    def select_image(self, image):
        lines = self.env_file.read_text().splitlines(keepends=True)
        prefix = "COUNCIL_HERMES_IMAGE="
        updated = [prefix + image + "\n" if line.startswith(prefix) else line for line in lines]
        if not any(line.startswith(prefix) for line in lines):
            if updated and not updated[-1].endswith("\n"):
                updated[-1] += "\n"
            updated.append(prefix + image + "\n")
        atomic_write(self.env_file, "".join(updated).encode())
