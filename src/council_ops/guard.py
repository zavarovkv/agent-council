"""Observe other services; never start, stop or reconfigure them."""

import hashlib
import json
import re
import subprocess
import urllib.request
from urllib.parse import urlsplit

from .config import OperationError


class ServiceGuard:
    def __init__(self, path):
        self.entries = []
        if not path.exists():
            return
        if path.is_symlink():
            raise OperationError("Refusing a symlinked protected-service configuration.")
        for entry in json.loads(path.read_text()).get("services", []):
            if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.@-]*\.service", entry["unit"]):
                raise OperationError("Invalid protected-service unit.")
            origin = urlsplit(entry["url"])
            if (
                origin.scheme != "https"
                or not origin.hostname
                or origin.username
                or origin.password
            ):
                raise OperationError(
                    "Protected service probes require HTTPS without URL credentials."
                )
            process = self.process(entry["unit"])
            if process == "0":
                raise OperationError("A protected service is not running.")
            self.entries.append((entry, process))
        self.check()

    @staticmethod
    def process(unit):
        result = subprocess.run(
            ["systemctl", "show", unit, "-p", "MainPID", "--value"],
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
        return result.stdout.strip()

    def check(self):
        for entry, process in self.entries:
            if self.process(entry["unit"]) != process:
                raise OperationError("A protected service process changed during the operation.")
            for filename, expected in entry.get("sha256", {}).items():
                with open(filename, "rb") as source:
                    if hashlib.file_digest(source, "sha256").hexdigest() != expected:
                        raise OperationError("A protected service configuration changed.")
            with urllib.request.urlopen(entry["url"], timeout=15) as response:
                if response.status != 200:
                    raise OperationError("A protected service did not return HTTP 200.")
