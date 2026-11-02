# Native owner requests

The trusted main Hermes profile uses `council_tasks` to submit team work to the
isolated Linear adapter. The adapter creates the root task through Qui-Gon's
application authorization, sets Qui-Gon as delegate and explicitly clears the
human assignee. The existing Linear coordinator creates specialist children;
specialists cannot delegate to each other. Model credentials, app grants and
native task keys remain outside Git.

`create` requires a stable request key and bounded brief. Repeating the same
request returns the same task; an uncertain network response must be reconciled
with `get` before retrying. `list` discovers existing admissions and `get` returns
the actual task, execution and specialist status. `reply` forwards an owner
clarification to that same coordinator session, including a pending question.
A queued receipt does not prove an agent started. Completion in Linear does not
prove Telegram delivery; this intake does not add a delivery schedule.

Deploy an image with `native-task-routing-v1`, then run
`python3 scripts/manage.py configure-native-tasks` on the host. The operator uses
the maintenance lock, idle check, backup and copied-profile test before activating
the configuration and restarting Hermes, with rollback on failure. The provisioner
creates a separate private bearer grant pinned to the registry owner and team.
It preserves OAuth configuration and other MCP servers while limiting personal
Linear MCP tools to `get_*`, `list_*` and `search_*`; team mutations use the app
route. It is idempotent and rejects changed owner/destination grants.

The endpoint `/coordinator/tasks` exists only on the internal adapter listener.
Do not expose it through Nginx or copy the main-profile key to specialist/public
profiles. Changing registry owner, team or policy revokes existing admissions.
Keep the grant when rolling forward; the operator rejects rollback to an image
without the capability once the main profile has been configured.

For repeated report requests, read the existing task and artifact once. Return
the saved result or the actual outstanding dependency; do not reassign the same
failed specialist repeatedly or conduct duplicate research in the native chat.
