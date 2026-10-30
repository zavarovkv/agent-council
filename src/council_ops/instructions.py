"""Install a reviewed private instruction checkout without sharing its Git credentials."""

import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from secretary_worker.instructions import load_instructions

from .config import OperationError, atomic_write, contained, read_settings
from .lifecycle import identity, restore, smoke
from .snapshots import snapshot

MARKER = ".council/instructions.json"
ROLE = re.compile(r"[a-z][a-z0-9_-]{0,63}\Z")
CREDENTIAL = re.compile(
    r"gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}|"
    r"sk-(?:proj-)?[A-Za-z0-9_-]{30,}|GOCSPX-[A-Za-z0-9_-]{15,}|"
    r"[0-9]{7,}:[A-Za-z0-9_-]{30,}|(?:AKIA|ASIA)[A-Z0-9]{16}|"
    r"-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----|(?:ntn_|secret_)[A-Za-z0-9]{30,}"
)


@dataclass(frozen=True)
class Artifact:
    data: bytes = field(repr=False)
    executable: bool = False


def digest(data):
    return hashlib.sha256(data).hexdigest()


def valid_destination(name):
    if not isinstance(name, str):
        return False
    parts = PurePosixPath(name).parts
    if not parts or str(PurePosixPath(name)) != name or any(p in {"..", "."} for p in parts):
        return False
    if parts[:3] == (".council", "secretary", "instructions"):
        return name in {
            ".council/secretary/instructions/POLICY.md",
            *(
                f".council/secretary/instructions/skills/{role}/SKILL.md"
                for role in ("spouse", "contacts", "new-users")
            ),
        }
    if parts[0] == "profiles":
        if len(parts) < 3 or not ROLE.fullmatch(parts[1]):
            return False
        parts = parts[2:]
    return parts in (("SOUL.md",), ("AGENTS.md",)) or (
        len(parts) >= 3
        and parts[0] == "skills"
        and all(not part.startswith(".") for part in parts)
        and Path(parts[-1]).suffix not in {".env", ".key", ".pem", ".db", ".sqlite", ".sqlite3"}
    )


def destination(home, name):
    if not valid_destination(name):
        raise OperationError("Invalid managed instruction destination.")
    path = home / name
    if path.resolve() != path or (path.exists() and not path.is_file()):
        raise OperationError("Refusing a symlinked or non-file instruction destination.")
    return path


def collect(source, home):
    """Read bounded text artifacts only; never import a whole Hermes profile."""
    files = {}
    roles = [path for path in source.iterdir() if path.name != ".git" and path.is_dir()]
    for role in roles:
        if role.is_symlink() or not ROLE.fullmatch(role.name):
            raise OperationError("Invalid instruction role directory.")
        if role.name == "secretary":
            load_instructions(role)
            if not (home / ".council/secretary/.council-secretary.json").is_file():
                raise OperationError("Prepare the secretary profile before installing its rules.")
            prefix = ".council/secretary/instructions"
        elif role.name == "assistant":
            prefix = ""
        else:
            if not (home / "profiles" / role.name / "config.yaml").is_file():
                raise OperationError("Provision each named agent profile before its instructions.")
            prefix = "profiles/" + role.name
        for path in role.rglob("*"):
            if path.is_symlink():
                raise OperationError("Instruction bundles must not contain symlinks.")
            if path.is_dir():
                continue
            relative = path.relative_to(role)
            if relative == Path("README.md"):
                continue
            name = str(PurePosixPath(prefix) / relative)
            if not valid_destination(name) or not path.is_file() or path.stat().st_size > 1048576:
                raise OperationError("Invalid or oversized instruction artifact.")
            if relative.parts[0] == "skills" and not any(
                (parent / "SKILL.md").is_file()
                for parent in path.parents
                if contained(parent, role / "skills")
            ):
                raise OperationError("Skill resources require a parent SKILL.md.")
            data = path.read_bytes()
            if CREDENTIAL.search(data.decode("utf-8")):
                raise OperationError("Credential-like content found; keep credentials outside Git.")
            files[name] = Artifact(data, bool(path.stat().st_mode & 0o111))
    if not files or len(files) > 1024 or sum(len(f.data) for f in files.values()) > 8 * 1048576:
        raise OperationError("An instruction bundle must contain bounded role artifacts.")
    return files


def previous_files(home):
    path = home / MARKER
    if path.resolve() != path:
        raise OperationError("Refusing a symlinked instruction ownership marker.")
    if not path.exists():
        return {}
    value = json.loads(path.read_text())
    if (
        not isinstance(value, dict)
        or value.get("schema") != 1
        or not isinstance(value.get("files"), dict)
    ):
        raise OperationError("Invalid instruction ownership marker.")
    for name, checksum in value["files"].items():
        destination(home, name)
        if not isinstance(checksum, str) or not re.fullmatch(r"[a-f0-9]{64}", checksum):
            raise OperationError("Invalid instruction checksum.")
    return value["files"]


def validate_existing(home, files):
    previous = previous_files(home)
    for name in previous.keys() | files.keys():
        path = destination(home, name)
        if name in previous:
            if not path.is_file() or digest(path.read_bytes()) != previous[name]:
                # A reviewed export may adopt these exact local edits, never overwrite them.
                if name not in files or not path.is_file() or path.read_bytes() != files[name].data:
                    raise OperationError(
                        "Managed instructions changed locally; export and reconcile first."
                    )
        elif path.exists() and path.read_bytes() != files[name].data:
            raise OperationError(
                "Unmanaged instructions differ; preserve and reconcile them first."
            )
    return previous


def install_files(home, files, revision, uid, gid):
    previous = validate_existing(home, files)
    for name, artifact in files.items():
        path = destination(home, name)
        missing = [
            parent for parent in path.parents if contained(parent, home) and not parent.exists()
        ]
        for parent in reversed(missing):
            parent.mkdir(mode=0o700)
            os.chown(parent, uid, gid)
        atomic_write(path, artifact.data, mode=0o700 if artifact.executable else 0o600)
        os.chown(path, uid, gid)
    for name in previous.keys() - files.keys():
        destination(home, name).unlink()
    marker = home / MARKER
    marker.parent.mkdir(mode=0o700, exist_ok=True)
    os.chown(marker.parent, uid, gid)
    atomic_write(
        marker,
        (
            json.dumps(
                {
                    "schema": 1,
                    "revision": revision,
                    "files": {
                        name: digest(artifact.data) for name, artifact in sorted(files.items())
                    },
                },
                indent=2,
            )
            + "\n"
        ).encode(),
    )
    os.chown(marker, uid, gid)


def checkout(runtime, source, revision, *, fetch=False):
    source = source.absolute()
    if (
        source.resolve() != source
        or not source.is_dir()
        or not re.fullmatch(r"[a-f0-9]{40}", revision)
    ):
        raise OperationError("Use a regular private checkout and a full reviewed commit SHA.")
    settings = runtime.settings
    for private in (
        settings.repository,
        settings.data_dir,
        settings.workspace_dir,
        settings.backup_dir,
    ):
        if contained(source, private) or contained(private, source):
            raise OperationError(
                "Keep the private checkout separate from runtime, public code and backups."
            )
    git = ["git", "-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null", "-C", str(source)]
    if runtime.run([*git, "rev-parse", "--show-toplevel"]) != str(source) or runtime.run(
        [*git, "status", "--porcelain", "--untracked-files=all", "--ignored"]
    ):
        raise OperationError("Install a clean private checkout at its reviewed revision.")
    if fetch:
        runtime.run([*git, "fetch", "--no-tags", "origin", revision])
        runtime.run([*git, "switch", "--detach", revision])
    if runtime.run([*git, "rev-parse", "HEAD"]) != revision:
        raise OperationError("The private checkout differs from the reviewed revision.")
    return git


def apply(runtime, source, revision, *, fetch=False):
    source = source.absolute()
    checkout(runtime, source, revision, fetch=fetch)
    files = collect(source, runtime.settings.data_dir)
    validate_existing(runtime.settings.data_dir, files)
    checkout(runtime, source, revision)
    marker = runtime.settings.data_dir / MARKER
    has_secretary = any(name.startswith(".council/secretary/instructions/") for name in files)
    if has_secretary:
        uid, gid = identity(runtime.settings)
        try:
            runtime.run(
                [
                    "docker",
                    "run",
                    "--rm",
                    "--network",
                    "none",
                    "--read-only",
                    "--user",
                    f"{uid}:{gid}",
                    "--cap-drop",
                    "ALL",
                    "--security-opt",
                    "no-new-privileges:true",
                    "--memory",
                    "128m",
                    "--pids-limit",
                    "32",
                    "-e",
                    "PYTHONDONTWRITEBYTECODE=1",
                    "--workdir",
                    "/opt/agent-council",
                    "--entrypoint",
                    "/opt/hermes/.venv/bin/python",
                    runtime.image()[0],
                    "-c",
                    "from secretary_worker.instructions import configured_instructions",
                ]
            )
        except OperationError:
            raise OperationError(
                "Deploy the compatible private-instruction image before applying secretary rules."
            ) from None
    had_secretary = any(
        name.startswith(".council/secretary/instructions/")
        for name in previous_files(runtime.settings.data_dir)
    )
    selector_matches = (
        not has_secretary
        or read_settings(runtime.settings.secretary_file).get(
            "COUNCIL_SECRETARY_INSTRUCTIONS", "image"
        )
        == "profile"
    )
    if marker.is_file() and json.loads(marker.read_text()).get("revision") == revision:
        if selector_matches and previous_files(runtime.settings.data_dir) == {
            name: digest(artifact.data) for name, artifact in files.items()
        }:
            return {
                "instructions_revision": revision,
                "files_installed": len(files),
                "changed": False,
            }
    backup = snapshot(runtime, restart=False)
    changed = False
    try:
        uid, gid = identity(runtime.settings)
        smoke(
            runtime,
            backup,
            prepare_copy=lambda home: install_files(home, files, revision, uid, gid),
        )
        changed = True
        install_files(runtime.settings.data_dir, files, revision, uid, gid)
        if has_secretary or had_secretary:
            values = read_settings(runtime.settings.secretary_file)
            values["COUNCIL_SECRETARY_INSTRUCTIONS"] = "profile" if has_secretary else "image"
            atomic_write(
                runtime.settings.secretary_file,
                "".join(f"{key}={value}\n" for key, value in values.items()).encode(),
            )
        runtime.start()
        runtime.ready()
        runtime.check_protected()
    except Exception:
        if changed:
            restore(runtime, backup, require_idle=False)
        else:
            runtime.start()
            runtime.ready()
        raise OperationError(
            "Instruction installation failed; previous service and state restored."
        ) from None
    return {"instructions_revision": revision, "files_installed": len(files), "backup": str(backup)}


def source_name(name):
    parts = PurePosixPath(name).parts
    if parts[:3] == (".council", "secretary", "instructions"):
        return PurePosixPath("secretary", *parts[3:])
    if parts[0] == "profiles":
        return PurePosixPath(parts[1], *parts[2:])
    return PurePosixPath("assistant", *parts)


def export(runtime, source, revision, *, include_skills=()):
    """Stage private working-copy edits for human review; never commit or push them."""
    source = source.absolute()
    checkout(runtime, source, revision)
    home = runtime.settings.data_dir
    previous = previous_files(home)
    if not previous or json.loads((home / MARKER).read_text()).get("revision") != revision:
        raise OperationError("Export into the clean checkout of the currently installed revision.")
    runtime.require_idle()
    names = set(previous)
    for skill in include_skills:
        directory = home / "skills" / skill
        if (
            not valid_destination("skills/" + skill + "/SKILL.md")
            or directory.resolve() != directory
            or not (directory / "SKILL.md").is_file()
        ):
            raise OperationError("Select an existing relative skill directory without symlinks.")
        for path in directory.rglob("*"):
            if path.is_symlink():
                raise OperationError("Refusing a symlinked skill resource.")
            if path.is_file():
                names.add(str(path.relative_to(home)))
    artifacts = {}
    for name in names:
        path = destination(home, name)
        if not path.is_file() or path.stat().st_size > 1048576:
            raise OperationError("Missing or oversized runtime instruction.")
        data = path.read_bytes()
        if CREDENTIAL.search(data.decode("utf-8")):
            raise OperationError("Credential-like content found; export refused.")
        target = source / source_name(name)
        if target.resolve() != target:
            raise OperationError("Refusing a symlinked export destination.")
        artifacts[target] = Artifact(data, bool(path.stat().st_mode & 0o111))
    if len(artifacts) > 1024 or sum(len(f.data) for f in artifacts.values()) > 8 * 1048576:
        raise OperationError("Instruction export exceeds its size limit.")
    for target, artifact in artifacts.items():
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        atomic_write(target, artifact.data, mode=0o700 if artifact.executable else 0o600)
    return {"files_exported": len(artifacts), "commit_required": True, "service_restarted": False}
