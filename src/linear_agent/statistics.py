"""Periodically publish only aggregate issue counts, outside the execution loop."""

import json
import logging
import os
import tempfile

from council_team.catalog import ROLES

QUERY = """query CouncilCompletedIssues($filter:IssueFilter!,$after:String){
issues(filter:$filter,first:250,after:$after,includeArchived:true){
nodes{id delegate{id} assignee{id}} pageInfo{hasNextPage endCursor}}}"""


def collect(service):
    registry = service.registry()
    users = {}
    connected = {slug: False for slug in ROLES}
    for slug in registry.agents:
        installed = service.store.installation(slug)
        if installed and installed["organization"] == registry.organization:
            users[installed["app_user"]] = slug
            connected[slug] = registry.enabled
    agent = next((a for slug, a in registry.agents.items() if slug in users.values()), None)
    if agent is None:
        raise ValueError("No installed application can read team statistics.")
    counts = {slug: 0 if slug in users.values() else None for slug in ROLES}
    after = None
    seen = set()
    for _ in range(40):
        page = service.linear.query(
            agent,
            QUERY,
            {
                "filter": {
                    "team": {"id": {"in": sorted(registry.teams)}},
                    "state": {"type": {"eq": "completed"}},
                },
                "after": after,
            },
        )["issues"]
        for issue in page["nodes"]:
            if issue["id"] in seen:
                continue
            seen.add(issue["id"])
            # A delegated issue belongs to its executor, not also to its human assignee.
            user = (issue.get("delegate") or issue.get("assignee") or {}).get("id")
            slug = users.get(user)
            if slug and counts[slug] is not None:
                counts[slug] += 1
        if not page["pageInfo"]["hasNextPage"]:
            return {
                "schema": 1,
                "updated_at": service.clock(),
                "counts": counts,
                "connected": connected,
            }
        cursor = page["pageInfo"]["endCursor"]
        if not cursor or cursor == after:
            raise ValueError("Incomplete issue statistics pagination.")
        after = cursor
    raise ValueError("Issue statistics pagination limit reached.")


def publish(service):
    value = collect(service)
    descriptor, temporary = tempfile.mkstemp(prefix=".public-team-", dir=service.home)
    try:
        with os.fdopen(descriptor, "w") as output:
            json.dump(value, output)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, service.home / "public-team.json")
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def loop(service):
    while not service.halt.is_set():
        if not (service.home / "runtime/maintenance").exists():
            try:
                publish(service)
            except Exception:
                # Preserve the last successful aggregate and its age; no private diagnostics.
                if (service.home / "agents.json").exists():
                    logging.getLogger(__name__).warning(
                        "Public team counters could not be refreshed."
                    )
        service.halt.wait(300)
