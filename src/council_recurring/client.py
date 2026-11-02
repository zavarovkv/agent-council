"""Trusted main-profile management; cloud and executor credentials stay elsewhere."""

import fcntl
import json
import os
import re
import tempfile
import urllib.request
from contextlib import contextmanager

from .contract import definition, revision, start_schedule


def home_path():
    from hermes_constants import get_hermes_home

    return get_hermes_home()


def settings(home):
    path = home / ".council/recurring/client.json"
    if path.resolve() != path or path.stat().st_mode & 0o077 or path.stat().st_size > 8192:
        raise PermissionError("Invalid recurring client grant.")
    value = json.loads(path.read_text())
    if (
        set(value) != {"schema", "key", "team", "project", "notion_parent", "delivery"}
        or value["schema"] != 1
        or not re.fullmatch(r"[a-f0-9]{64}", value["key"])
        or not re.fullmatch(r"telegram:[1-9][0-9]{0,19}", value["delivery"])
    ):
        raise ValueError("Invalid recurring client settings.")
    return value


def call(home, payload):
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *_args, **_kwargs):
            return None

    request = urllib.request.Request(
        "http://linear-adapter:9122/recurring/tasks",
        data=json.dumps(payload, ensure_ascii=False).encode(),
        headers={
            "Authorization": "Bearer " + settings(home)["key"],
            "Content-Type": "application/json",
        },
        method="POST",
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    with opener.open(request, timeout=20) as response:
        data = response.read(1048577)
    if len(data) > 1048576:
        raise ValueError("Oversized recurring response.")
    return json.loads(data)


def atomic_json(path, value):
    if path.resolve() != path:
        raise PermissionError("Refusing a symlinked recurring file.")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as out:
        os.fchmod(out.fileno(), 0o600)
        out.write(json.dumps(value, ensure_ascii=False, indent=2).encode() + b"\n")
        out.flush()
        os.fsync(out.fileno())
    os.replace(out.name, path)


@contextmanager
def locked(home):
    directory = home / ".council/recurring"
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    with os.fdopen(
        os.open(directory / ".lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600), "a"
    ) as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def manifest(home):
    path = home / ".council/recurring/jobs.json"
    if not path.exists():
        return {}
    if path.resolve() != path or path.stat().st_size > 262144:
        raise ValueError("Invalid recurring manifest.")
    entries = json.loads(path.read_text())
    if not isinstance(entries, dict) or len(entries) > 32:
        raise ValueError("Invalid recurring manifest.")
    for identity, entry in entries.items():
        value = definition(entry["definition"])
        if (
            identity != value["id"]
            or entry["revision"] != revision(value)
            or type(entry["enabled"]) is not bool
            or not isinstance(entry["jobs"], dict)
            or not set(entry["jobs"]) <= {"start", "delivery"}
        ):
            raise ValueError("Invalid recurring manifest entry.")
        if any(
            not isinstance(i, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", i)
            for i in entry["jobs"].values()
        ):
            raise ValueError("Invalid native job identity.")
    return entries


def configure(home, value, *, native=None, remote=call, adopt=None):
    if native is None:
        from cron import jobs as native
    value = definition(value)
    grant = settings(home)
    with locked(home):
        entries = manifest(home)
        if value["id"] not in entries and len(entries) >= 32:
            raise ValueError("At most 32 team schedules are supported.")
        entry = entries.get(value["id"], {"jobs": {}})
        if adopt is not None:
            if (
                set(adopt) != {"start", "delivery"}
                or len(set(adopt.values())) != 2
                or (entry["jobs"] and entry["jobs"] != adopt)
            ):
                raise ValueError("Select the two existing job IDs explicitly for migration.")
            entry["jobs"] = dict(adopt)
        for identity in entry["jobs"].values():
            job = native.get_job(identity)
            if not job or job.get("fire_claim"):
                raise BlockingIOError("Wait for the active schedule fire before changing it.")
            native.pause_job(identity, reason="Updating recurring definition")
        saved = remote(home, {"action": "configure", "definition": value})
        entry.update(
            {"definition": saved["definition"], "revision": saved["revision"], "enabled": False}
        )
        scripts = home / "scripts"
        scripts.mkdir(mode=0o700, parents=True, exist_ok=True)
        for phase in ("start", "delivery"):
            filename = f"council-{value['id']}-{phase}.py"
            path = scripts / filename
            source = (
                "import sys\nsys.path.insert(0, '/opt/hermes')\nfrom council_recurring.runner import main\nmain("
                + repr(value["id"])
                + ", "
                + repr(phase)
                + ")\n"
            )
            if path.resolve() != path:
                raise PermissionError("Refusing a symlinked recurring script.")
            path.write_text(source)
            path.chmod(0o600)
            fields = {
                "name": f"council-{value['id']}-{phase}",
                "prompt": f"{value['title']}. {phase}. Managed by recurring_tasks; delivery {value['delivery_time']} Europe/Moscow. No LLM in scheduler.",
                "schedule": start_schedule(value) if phase == "start" else "*/5 * * * *",
                "deliver": "local" if phase == "start" else grant["delivery"],
                "failure_deliver": "local",
                "script": filename,
                "interpreter": "/opt/hermes/.venv/bin/python",
                "no_agent": True,
                "skills": [],
                "skill": None,
                "context_from": [],
            }
            identity = entry["jobs"].get(phase)
            if not identity:
                candidates = [
                    j
                    for j in native.list_jobs(include_disabled=True)
                    if j.get("name") == fields["name"]
                ]
                if len(candidates) > 1 or (candidates and candidates[0].get("script") != filename):
                    raise ValueError("Reconcile conflicting existing schedule names.")
                identity = (
                    candidates[0]["id"]
                    if candidates
                    else native.create_job(**fields, paused=True)["id"]
                )
                entry["jobs"][phase] = identity
                entries[value["id"]] = entry
                atomic_json(home / ".council/recurring/jobs.json", entries)
            native.update_job(identity, fields)
        entry["enabled"] = True
        entries[value["id"]] = entry
        atomic_json(home / ".council/recurring/jobs.json", entries)
        for identity in entry["jobs"].values():
            native.resume_job(identity)
        return describe(entry, native)


def describe(entry, native):
    return {
        "definition": entry["definition"],
        "enabled": entry["enabled"],
        "timezone": "Europe/Moscow",
        "revision": entry["revision"],
        "jobs": {
            phase: {
                k: job.get(k)
                for k in ("id", "enabled", "next_run_at", "last_status", "last_run_at")
            }
            for phase, identity in entry["jobs"].items()
            if (job := native.get_job(identity))
        },
    }


def manage(args, *, home=None):
    from cron import jobs

    home = home or home_path()
    grant = settings(home)
    action = args.get("action")
    if action == "configure":
        value = {
            k: args[k]
            for k in (
                "id",
                "title",
                "brief",
                "start_time",
                "cutoff_time",
                "delivery_time",
                "weekdays",
                "window_days",
                "telegram_source",
            )
        }
        value.update({k: grant[k] for k in ("team", "project", "notion_parent")})
        return configure(home, value)
    if action == "list":
        return {"schedules": [describe(e, jobs) for e in manifest(home).values()]}
    if action == "status":
        return call(home, {"action": "list"})
    if action in {"pause", "resume"}:
        with locked(home):
            entries = manifest(home)
            entry = entries[args["id"]]
            entry["enabled"] = action == "resume"
            atomic_json(home / ".council/recurring/jobs.json", entries)
            for identity in entry["jobs"].values():
                (jobs.resume_job if entry["enabled"] else jobs.pause_job)(identity)
            return describe(entry, jobs)
    raise ValueError("Use configure, list, status, pause or resume.")


def register(ctx):
    def available():
        try:
            settings(home_path())
            return True
        except (OSError, ValueError, PermissionError):
            return False

    def handler(args, **_kwargs):
        try:
            return json.dumps(manage(args), ensure_ascii=False)
        except Exception:
            return json.dumps(
                {
                    "error": "Recurring operation was not confirmed. Read list/status and reconcile the existing schedule; do not create a duplicate or promise delivery."
                }
            )

    description = "Manage recurring team reports through native Hermes Cron and Linear. configure creates or updates the SAME stable id; list/status shows actual schedules and runs; pause/resume changes future firing. Times are Europe/Moscow, weekdays Monday=0. Owner delivery and shared project destinations are pinned. A saved schedule is not proof of a successful report: inspect execution and delivery. Do not use native Kanban or research_archive for these jobs."
    properties = {
        "action": {"type": "string", "enum": ["configure", "list", "status", "pause", "resume"]}
    }
    for name in ("id", "title", "brief", "start_time", "cutoff_time", "delivery_time"):
        properties[name] = {"type": "string"}
    properties.update(
        {
            "weekdays": {"type": "array", "items": {"type": "integer"}},
            "window_days": {"type": "integer"},
            "telegram_source": {"type": "boolean"},
        }
    )
    ctx.register_tool(
        name="recurring_tasks",
        toolset="council_workspace",
        description=description,
        check_fn=available,
        handler=handler,
        schema={
            "name": "recurring_tasks",
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": ["action"],
                "additionalProperties": False,
            },
        },
    )
