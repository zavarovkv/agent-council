"""Install a reviewed private instruction checkout without sharing its Git credentials."""

import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

from linear_agent.config import SLUGS
from secretary_worker.instructions import load_instructions

from .config import OperationError, atomic_write, contained, read_settings
from .lifecycle import identity, restore, smoke
from .snapshots import snapshot

MARKER = ".council/instructions.json"
FAILURE = "instructions-update-failure.json"
ROLE = re.compile(r"[a-z][a-z0-9_-]{0,63}\Z")
CAPABILITY_LABEL = "io.agent-council.capabilities"
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


class InstructionReleaseError(OperationError):
    """A candidate failed after checkpointing; require a new commit or explicit retry."""


def clear_failure(settings):
    path = settings.env_file.with_name(FAILURE)
    if path.is_symlink():
        raise OperationError("Refusing a symlinked instruction failure record.")
    path.unlink(missing_ok=True)


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
    if parts[:2] == (".council", "linear"):
        if len(parts) < 4 or parts[2] not in SLUGS:
            return False
        parts = parts[3:]
    elif parts[0] == "profiles":
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
    bundles = []
    roles = [path for path in source.iterdir() if path.name != ".git" and path.is_dir()]
    for role in roles:
        if role.is_symlink() or not ROLE.fullmatch(role.name):
            raise OperationError("Invalid instruction role directory.")
        if role.name == "linear":
            if "agent-specialists-v1" not in source_requirements(source):
                raise OperationError("Linear instruction bundles require agent-specialists-v1.")
            for agent in role.iterdir():
                if agent.name not in SLUGS or agent.is_symlink() or not agent.is_dir():
                    raise OperationError("Invalid isolated Linear instruction role.")
                profile = home / ".council/linear" / agent.name
                if not (profile / "config.yaml").is_file():
                    raise OperationError("Prepare each isolated executor before its instructions.")
                prefix = ".council/linear/" + agent.name
                bundles.append((agent, prefix, True))
                alias = agent / "skills-source.json"
                if alias.exists() or alias.is_symlink():
                    if (
                        alias.resolve() != alias
                        or not alias.is_file()
                        or alias.stat().st_size > 256
                    ):
                        raise OperationError("Invalid shared skill source.")
                    value = json.loads(alias.read_text())
                    if (
                        not isinstance(value, dict)
                        or set(value) != {"role"}
                        or not isinstance(value["role"], str)
                        or not ROLE.fullmatch(value["role"])
                        or value["role"] in {"linear", "secretary"}
                    ):
                        raise OperationError("Invalid shared skill source.")
                    shared = source / value["role"]
                    if shared.resolve() != shared or not (shared / "skills").is_dir():
                        raise OperationError("Missing shared skill source.")
                    bundles.append((shared / "skills", prefix + "/skills", False))
            continue
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
        bundles.append((role, prefix, True))
    for role, prefix, full_role in bundles:
        for path in role.rglob("*"):
            if path.is_symlink():
                raise OperationError("Instruction bundles must not contain symlinks.")
            if path.is_dir():
                continue
            relative = path.relative_to(role)
            if relative == Path("README.md") or (
                relative == Path("skills-source.json") and prefix.startswith(".council/linear/")
            ):
                continue
            name = str(PurePosixPath(prefix) / relative)
            if (
                name in files
                or not valid_destination(name)
                or not path.is_file()
                or path.stat().st_size > 1048576
            ):
                raise OperationError("Duplicate, invalid or oversized instruction artifact.")
            skill_root = role / "skills" if full_role else role
            if (not full_role or relative.parts[0] == "skills") and not any(
                (parent / "SKILL.md").is_file()
                for parent in path.parents
                if contained(parent, skill_root)
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


def validate_existing(home, files, *, adoptions=None):
    previous = previous_files(home)
    for name, checksum in (adoptions or {}).items():
        path = destination(home, name)
        if (
            name in previous
            or name not in files
            or not isinstance(checksum, str)
            or not re.fullmatch(r"[a-f0-9]{64}", checksum)
            or not path.is_file()
            or digest(path.read_bytes()) != checksum
        ):
            raise OperationError("Adopt only an unmanaged candidate file at its reviewed checksum.")
        previous[name] = checksum
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


def validated_capabilities(value):
    if (
        not isinstance(value, list)
        or len(value) > 20
        or any(not isinstance(item, str) or not ROLE.fullmatch(item) for item in value)
    ):
        raise OperationError("Invalid instruction capability requirements.")
    return sorted(set(value))


def source_requirements(source):
    path = source / "requires.json"
    if not path.exists() and not path.is_symlink():
        return []
    if path.resolve() != path or not path.is_file() or path.stat().st_size > 4096:
        raise OperationError("Invalid instruction compatibility manifest.")
    value = json.loads(path.read_text())
    if (
        not isinstance(value, dict)
        or set(value) != {"schema", "capabilities"}
        or value["schema"] != 1
    ):
        raise OperationError("Unsupported instruction compatibility manifest.")
    return validated_capabilities(value["capabilities"])


def installed_requirements(home):
    path = home / MARKER
    if not path.exists():
        return []
    previous_files(home)
    return validated_capabilities(json.loads(path.read_text()).get("required_capabilities", []))


def verify_capabilities(runtime, required, *, image=None):
    if not required:
        return
    labels = (
        runtime.image()[1]
        if image is None
        else json.loads(runtime.run(["docker", "image", "inspect", image]))[0]
        .get("Config", {})
        .get("Labels", {})
        or {}
    )
    available = set(labels.get(CAPABILITY_LABEL, "").split(","))
    if not set(required) <= available:
        raise OperationError(
            "Deploy an image providing the private instructions' required capabilities first."
        )


def install_files(home, files, revision, uid, gid, *, capabilities=(), adoptions=None):
    previous = validate_existing(home, files, adoptions=adoptions)
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
                    "required_capabilities": list(capabilities),
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


def clean_checkout(runtime, source):
    source = source.absolute()
    if source.resolve() != source or not source.is_dir():
        raise OperationError("Use a regular private checkout.")
    if source.stat().st_uid != os.geteuid() or source.stat().st_mode & 0o077:
        raise OperationError("The private checkout must be operator-owned and mode 0700.")
    metadata = source / ".git"
    if (
        metadata.is_symlink()
        or not metadata.is_dir()
        or metadata.stat().st_uid != os.geteuid()
        or metadata.stat().st_mode & 0o022
    ):
        raise OperationError("Use a protected standalone Git metadata directory.")
    config = metadata / "config"
    if (
        config.is_symlink()
        or not config.is_file()
        or config.stat().st_uid != os.geteuid()
        or config.stat().st_mode & 0o022
    ):
        raise OperationError("Git configuration must not be writable by another account.")
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
    return git


def checkout(runtime, source, revision, *, fetch=False):
    if not re.fullmatch(r"[a-f0-9]{40}", revision):
        raise OperationError("Use a full reviewed commit SHA.")
    git = clean_checkout(runtime, source)
    if fetch:
        runtime.run([*git, "fetch", "--no-tags", "origin", revision])
        runtime.run([*git, "switch", "--detach", revision])
    if runtime.run([*git, "rev-parse", "HEAD"]) != revision:
        raise OperationError("The private checkout differs from the reviewed revision.")
    return git


def sync(runtime, source):
    """Follow the private main branch only after explicit initial installation."""
    if not previous_files(runtime.settings.data_dir):
        raise OperationError("Apply a reviewed private revision before enabling automatic updates.")
    git = clean_checkout(runtime, source)
    original = runtime.run([*git, "rev-parse", "HEAD"])
    if not re.fullmatch(r"[a-f0-9]{40}", original):
        raise OperationError("Invalid private source revision.")
    runtime.run([*git, "fetch", "--no-tags", "origin", "main"])
    candidate = runtime.run([*git, "rev-parse", "FETCH_HEAD"])
    if not re.fullmatch(r"[a-f0-9]{40}", candidate):
        raise OperationError("Invalid private update revision.")
    failure = runtime.settings.env_file.with_name(FAILURE)
    if failure.is_symlink():
        raise OperationError("Refusing a symlinked instruction failure record.")
    if failure.is_file() and json.loads(failure.read_text()).get("revision") == candidate:
        raise OperationError(
            "This private revision failed release validation; fix it in main or retry explicitly."
        )
    try:
        runtime.run([*git, "switch", "--detach", candidate])
        return apply(runtime, source, candidate)
    except InstructionReleaseError:
        atomic_write(
            failure,
            (
                json.dumps({"revision": candidate, "failed_at": datetime.now(UTC).isoformat()})
                + "\n"
            ).encode(),
        )
        runtime.run([*git, "switch", "--detach", original])
        raise
    except Exception:
        runtime.run([*git, "switch", "--detach", original])
        raise


def apply(runtime, source, revision, *, fetch=False, adoptions=None):
    source = source.absolute()
    checkout(runtime, source, revision, fetch=fetch)
    files = collect(source, runtime.settings.data_dir)
    capabilities = source_requirements(source)
    verify_capabilities(runtime, capabilities)
    validate_existing(runtime.settings.data_dir, files, adoptions=adoptions)
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
    if marker.is_file():
        same_files = previous_files(runtime.settings.data_dir) == {
            name: digest(artifact.data) for name, artifact in files.items()
        }
        same_modes = same_files and all(
            destination(runtime.settings.data_dir, name).stat().st_mode & 0o777
            == (0o700 if artifact.executable else 0o600)
            for name, artifact in files.items()
        )
        if selector_matches and same_files and same_modes:
            value = json.loads(marker.read_text())
            if (
                value.get("revision") != revision
                or value.get("required_capabilities", []) != capabilities
            ):
                value["revision"] = revision
                value["required_capabilities"] = capabilities
                atomic_write(marker, (json.dumps(value, indent=2) + "\n").encode())
                uid, gid = identity(runtime.settings)
                os.chown(marker, uid, gid)
            clear_failure(runtime.settings)
            return {
                "instructions_revision": revision,
                "files_installed": len(files),
                "changed": False,
            }
    backup = snapshot(runtime, restart=False, automatic=True)
    changed = False
    try:
        uid, gid = identity(runtime.settings)
        smoke(
            runtime,
            backup,
            prepare_copy=lambda home: install_files(
                home, files, revision, uid, gid, capabilities=capabilities, adoptions=adoptions
            ),
            instruction_mode="profile" if has_secretary else "image",
        )
        changed = True
        install_files(
            runtime.settings.data_dir,
            files,
            revision,
            uid,
            gid,
            capabilities=capabilities,
            adoptions=adoptions,
        )
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
        raise InstructionReleaseError(
            "Instruction installation failed; previous service and state restored."
        ) from None
    clear_failure(runtime.settings)
    return {"instructions_revision": revision, "files_installed": len(files), "backup": str(backup)}


def source_name(name, source=None):
    parts = PurePosixPath(name).parts
    if parts[:3] == (".council", "secretary", "instructions"):
        return PurePosixPath("secretary", *parts[3:])
    if parts[:2] == (".council", "linear"):
        alias = source / "linear" / parts[2] / "skills-source.json" if source else None
        if parts[3] == "skills" and alias and alias.is_file():
            return PurePosixPath(json.loads(alias.read_text())["role"], *parts[3:])
        return PurePosixPath("linear", parts[2], *parts[3:])
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
    collect(source, home)  # Validate role aliases before resolving export destinations.
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
        target = source / source_name(name, source)
        if target.resolve() != target:
            raise OperationError("Refusing a symlinked export destination.")
        artifact = Artifact(data, bool(path.stat().st_mode & 0o111))
        if target in artifacts and artifacts[target] != artifact:
            raise OperationError(
                "Shared skills diverged between runtimes; reconcile before export."
            )
        artifacts[target] = artifact
    if len(artifacts) > 1024 or sum(len(f.data) for f in artifacts.values()) > 8 * 1048576:
        raise OperationError("Instruction export exceeds its size limit.")
    for target, artifact in artifacts.items():
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        atomic_write(target, artifact.data, mode=0o700 if artifact.executable else 0o600)
    return {"files_exported": len(artifacts), "commit_required": True, "service_restarted": False}
