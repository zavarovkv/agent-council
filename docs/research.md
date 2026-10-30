# Research workflow

Hermes' native Kanban board is the source of task/run status. Council adds a
small filing outbox and coordinator-only `research_archive` tool; it does not
create another task database or give the secretary cloud tools.

## Setup

Prepare the existing coordinator Notion connection and
`council-workspace.json` → `knowledge_base.research.page_id`. Run on the host:

```bash
sudo python3 scripts/manage.py configure-research
sudo python3 scripts/manage.py deploy
sudo install -m 0644 config/agent-council-research.{service,timer} /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now agent-council-research.timer
```

Configuration takes a consistent checkpoint, creates the native `research` board
if absent and enables only the coordinator plugin's `notion` MCP allowlist.
Credentials stay in their existing native grant. The public DM/business worker
does not contain or import this plugin. Update the private coordinator/researcher
instructions to the released native-task workflow; public image defaults are in
[`instructions/`](../instructions/README.md).

To combine a code release and an already checked-out private instruction SHA in
one stopped cutover, use `deploy --instructions-source /private/checkout
--instructions-revision FULL_SHA`. Both copies are tested before live files are
changed. A failed activation restores image, instructions and state together.
Initial secretary role/source changes still use `apply-instructions` separately.

## Task, review and filing

Qui-Gon creates one native task assigned to `dexter`, supplies the complete brief
and observes its events/runs. The native gateway dispatcher owns execution; do
not start a second delegated worker for that same task. For example, inside Hermes:

```bash
hermes kanban --board research create 'Compare competitors' \
  --assignee dexter --body-file research-brief.md \
  --idempotency-key research-request-unique-id --max-runtime 20m --max-retries 1
hermes kanban --board research list
hermes kanban --board research runs TASK_ID
```

These commands start requested research and can spend the profile's model budget.
The gateway handles normal dispatch; `dispatch --dry-run` inspects a dispatcher
pass without spawning workers. Source quality still requires review: a task/run
record is execution bookkeeping, not proof that every claim is correct.

Dexter writes Markdown under its profile's `research/` directory and requests
review. After review/approval, Qui-Gon calls:

```json
{"action":"file","task_id":"TASK_ID","report_path":"dated-report.md"}
```

The tool requires a completed Dexter card and a closed research run. It rejects
path traversal, symlinks and reports over 32 KiB. A versioned Markdown attachment
is checksummed and linked from the native card. The report includes date, author,
reviewer and task/run IDs. Its stable identity reuses one Notion page and index
row across revisions. Other index rows and unrelated page content are preserved.

Notion normalizes Markdown, so first readback checks the parent and ordered text
tokens. The exact fetched content is then retained to detect later human edits
before overwriting a report. The immutable local attachment retains the exact
Markdown and SHA-256. An index update is read back before filing is confirmed.

For a retry, omit `report_path`; the stored reviewed version is reused. Action
`status` reports progress without cloud writes. Every five minutes the host timer
resumes one pending filing without model calls. Failed records rotate behind
other records; a persistent conflict does not monopolize the queue.

## Failure and recovery

Outbox state and immutable attachments live in
`.council/research-filing/`, outside Git, and are included in data backups. No
Notion credential is copied into the outbox, Docker build or CI. `doctor` exposes
only aggregate progress; owner monitoring reports stale/unavailable filing.

Remote create operations have no reliable idempotency token. After an ambiguous
response, the tool finds the marked child page and resumes it. If no page is
visible, it pauses that create rather than blindly repeating it. Initial index
creation/first-row ambiguity is handled the same way. Confirm the provider's
external state before operator reconciliation; do not delete the outbox to retry.
Human report edits, duplicate pages/rows, a moved destination or multiple matching
index tables require review and are never silently overwritten.

Only the coordinator can use this tool. Invited dashboard operators remain trusted
users of its shared runtime; this is not a tenant or untrusted-agent permission model.
