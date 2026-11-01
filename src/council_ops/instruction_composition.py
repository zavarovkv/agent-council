"""Compose private instructions from declarative, bounded canonical sources.

The operator never executes a compiler or other code from the private checkout.
"""

import json
import re
from dataclasses import dataclass
from pathlib import PurePosixPath

from .config import OperationError

CAPABILITY = "instruction-composition-v1"
NAME = re.compile(r"[a-z][a-z0-9-]{0,63}\Z")


def read_source(source, relative, *, limit=1048576):
    path = source / relative
    if path.resolve() != path or not path.is_file() or path.stat().st_size > limit:
        raise OperationError(f"Invalid composition source: {relative}")
    return path.read_text(encoding="utf-8")


def named_list(value):
    return (
        isinstance(value, list)
        and 0 < len(value) <= 64
        and all(isinstance(name, str) and NAME.fullmatch(name) for name in value)
        and len(set(value)) == len(value)
    )


@dataclass(frozen=True)
class Composition:
    roles: tuple = ()
    skills: tuple = ()
    linear: tuple = ()  # (executor slug, canonical role, adapter name)

    def shared_bundles(self, source, role, prefix):
        if role not in self.roles:
            return []
        return [
            (source / "shared/skills" / name, f"{prefix}/skills/{name}".lstrip("/"), False)
            for name in self.skills
        ]

    def souls(self, source):
        result = {}
        for slug, role, adapter in self.linear:
            name = f".council/linear/{slug}/SOUL.md"
            parts = (
                f"{role}/SOUL.md",
                "shared/linear-contract.md",
                f"adapters/linear/{adapter}.md",
            )
            result[name] = (
                "\n\n".join(read_source(source, part).strip() for part in parts) + "\n"
            ).encode()
        return result

    def canonical(self, relative):
        parts = relative.parts
        if (
            len(parts) >= 4
            and parts[0] in self.roles
            and parts[1] == "skills"
            and parts[2] in self.skills
        ):
            return PurePosixPath("shared", *parts[1:])
        return relative


def load(source, roles, capabilities):
    path = source / "composition.json"
    if not path.exists() and not path.is_symlink():
        return Composition()
    if CAPABILITY not in capabilities:
        raise OperationError(f"Composed instructions require {CAPABILITY}.")
    try:
        value = json.loads(read_source(source, "composition.json", limit=4096))
        if (
            not isinstance(value, dict)
            or set(value) != {"schema", "shared", "linear"}
            or type(value["schema"]) is not int
            or value["schema"] != 1
        ):
            raise ValueError
        shared, linear = value["shared"], value["linear"]
        if (
            not isinstance(shared, dict)
            or set(shared) != {"roles", "skills"}
            or not named_list(shared["roles"])
            or not named_list(shared["skills"])
        ):
            raise ValueError
        active = {role.name for role in roles}
        if not set(shared["roles"]) <= active - {"linear", "secretary"}:
            raise ValueError
        if not isinstance(linear, dict) or len(linear) > 16:
            raise ValueError
        if "linear" not in active or set(linear) != {p.name for p in (source / "linear").iterdir()}:
            raise ValueError
        entries = []
        for slug, adapter in linear.items():
            if (
                not NAME.fullmatch(slug)
                or not isinstance(adapter, str)
                or not NAME.fullmatch(adapter)
            ):
                raise ValueError
            alias = json.loads(read_source(source, f"linear/{slug}/skills-source.json", limit=256))
            if (
                not isinstance(alias, dict)
                or set(alias) != {"role"}
                or alias["role"] not in shared["roles"]
            ):
                raise ValueError
            # A checked-in generated SOUL could silently outrank the canonical role.
            if (source / "linear" / slug / "SOUL.md").exists():
                raise OperationError(
                    "Remove generated Linear SOUL copies; edit the canonical role or adapter."
                )
            entries.append((slug, alias["role"], adapter))
        for skill in shared["skills"]:
            read_source(source, f"shared/skills/{skill}/SKILL.md", limit=65536)
        return Composition(tuple(shared["roles"]), tuple(shared["skills"]), tuple(entries))
    except (ValueError, KeyError, TypeError) as error:
        raise OperationError("Invalid instruction composition manifest.") from error
