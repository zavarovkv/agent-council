"""Provision owned native cron jobs, initially paused, without altering other jobs."""

import hashlib
import json
import os
import tempfile
import uuid
from datetime import UTC
from pathlib import Path

from .config import source_name
from .delivery import read_json

SCRIPT = """from telegram_monitor.delivery import main
main()
"""
PROMPT = """Read the travel-digest skill and the managed telegram-monitor.json settings.
If those settings are not enabled, stop without starting work or sending anything.
Prepare today's reviewed travel/OTA competitor report for the latest 07:15 Moscow
cutoff. Read only today's source artifact in /workspace/monitoring/telegram/raw;
never use stale evidence as a successful scan. Delegate one bounded real research
task to the native Dexter research board, review the result, file it with
research_archive to the configured private research section, and persist the
task_id, reporting window and concise verified summary in today's reports JSON.
Use primary public sources as well as the explicitly bounded Telegram evidence.
If filing is pending, preserve the same task and handoff; do not repeat research.
Continue actionable work and ask the owner a precise blocking question when
needed. Do not send Telegram messages here: the native 08:00 delivery job sends
the verified report or an explicit failure status. Never fabricate source access,
events, task execution, publication or a scheduled retry.
"""
PAYLOAD_FIELDS = ("name", "prompt", "skills", "script", "no_agent", "deliver", "schedule")


def atomic_json(path, value):
    path = Path(path)
    if path.resolve() != path or (path.exists() and not path.is_file()):
        raise ValueError("Unsafe managed monitor file.")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as output:
            temporary = Path(output.name)
            os.fchmod(output.fileno(), 0o600)
            json.dump(value, output, ensure_ascii=False, indent=2)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def fingerprint(job):
    return hashlib.sha256(
        json.dumps({k: job.get(k) for k in PAYLOAD_FIELDS}, sort_keys=True).encode()
    ).hexdigest()


def provision(home, source, peer, owner, jobs, *, activate=False):
    source = source_name(source)
    if type(peer) is not int or peer <= 0 or type(owner) is not int or not 0 < owner < 2**63:
        raise ValueError("Explicit pinned source and owner IDs are required.")
    # Pinned Hermes has no per-job timezone. Do not silently shift existing jobs.
    zone = jobs.parse_schedule.__globals__["_hermes_now"]().tzinfo
    if str(zone) not in {"UTC", "Etc/UTC"} or zone.utcoffset(None) not in {
        None,
        UTC.utcoffset(None),
    }:
        raise ValueError("This release requires the native scheduler's UTC timezone.")
    marker = home / ".council/telegram-monitor.json"
    identity = {
        "schema": 1,
        "managed_by": "agent-council",
        "source": source,
        "peer_id": peer,
        "owner_id": owner,
    }
    config = (
        read_json(marker)
        if marker.exists()
        else {**identity, "nonce": uuid.uuid4().hex, "enabled": False, "jobs": {}}
    )
    if any(config.get(k) != v for k, v in identity.items()):
        raise ValueError(
            "Preserve the existing source and owner pins; reconcile changes explicitly."
        )
    if not (home / "skills/travel-digest/SKILL.md").is_file():
        raise ValueError("Install the reviewed travel-digest skill before scheduling.")
    script = home / "scripts/council-deliver-travel.py"
    script.parent.mkdir(mode=0o700, exist_ok=True)
    data = ("import sys\nsys.path.insert(0, '/opt/agent-council')\n" + SCRIPT).encode()
    checksum = hashlib.sha256(data).hexdigest()
    if script.resolve() != script or (
        script.exists()
        and hashlib.sha256(script.read_bytes()).hexdigest()
        not in {checksum, config.get("script_sha256")}
    ):
        raise ValueError("Preserve local delivery-script edits before updating.")
    if not script.exists():
        with script.open("xb") as output:
            os.fchmod(output.fileno(), 0o600)
            output.write(data)
    elif script.read_bytes() != data:
        raise ValueError("Upgrade the managed delivery script explicitly before rescheduling.")
    config["script_sha256"] = checksum
    atomic_json(marker, config)  # Persist ownership before creating either native job.
    definitions = {
        "research": {
            "schedule": "20 4 * * *",
            "prompt": PROMPT,
            "skills": ["travel-digest"],
            "deliver": "local",
        },
        "delivery": {
            "schedule": "0 5 * * *",
            "prompt": "",
            "script": str(script),
            "no_agent": True,
            "deliver": f"telegram:{owner}",
        },
    }
    for role, definition in definitions.items():
        name = f"Council travel {role} {config['nonce'][:12]}"
        owned = config["jobs"].get(role)
        job = jobs.get_job(owned["id"]) if owned else None
        if owned and (not job or fingerprint(job) != owned["sha256"]):
            raise ValueError("A managed cron job was edited or removed; preserve and reconcile it.")
        if not owned:
            candidates = [j for j in jobs.list_jobs(include_disabled=True) if j.get("name") == name]
            if len(candidates) > 1:
                raise ValueError("Conflicting managed cron jobs.")
            job = (
                candidates[0]
                if candidates
                else jobs.create_job(
                    name=name,
                    paused=True,
                    paused_reason="Awaiting verified source access",
                    **definition,
                )
            )
            expected_schedule = jobs.parse_schedule(definition["schedule"])
            if (
                job.get("name") != name
                or job.get("schedule") != expected_schedule
                or any(job.get(k) != v for k, v in definition.items() if k != "schedule")
            ):
                raise ValueError("A recovered cron job differs from the reviewed definition.")
            config["jobs"][role] = {"id": job["id"], "sha256": fingerprint(job)}
            atomic_json(marker, config)
    if activate:
        if not config["enabled"]:
            for owned in config["jobs"].values():
                jobs.update_job(
                    owned["id"],
                    {
                        "enabled": True,
                        "state": "scheduled",
                        "paused_at": None,
                        "paused_reason": None,
                    },
                )
        config["enabled"] = True
        atomic_json(marker, config)
    return {
        "configured": True,
        "enabled": config["enabled"],
        "jobs": {k: v["id"] for k, v in config["jobs"].items()},
        "timezone": "Europe/Moscow",
        "research_at": "07:20",
        "delivery_at": "08:00",
    }


def main():
    import sys

    import cron.jobs as jobs
    from hermes_constants import get_default_hermes_root

    home = Path(os.environ["HERMES_HOME"])
    if os.geteuid() == 0 or home.resolve() != get_default_hermes_root().resolve():
        raise ValueError("Configure only the trusted coordinator as its runtime user.")
    source, peer, owner, activate = sys.argv[1:]
    print(
        json.dumps(
            provision(home, source, int(peer), int(owner), jobs, activate=activate == "activate")
        )
    )


if __name__ == "__main__":
    main()
