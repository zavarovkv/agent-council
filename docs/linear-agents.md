# Native Linear agents

Linear supplies the issue and discussion UI; Hermes executes the work. This
optional integration uses [Linear's Agents API](https://linear.app/developers/agents)
and the pinned Hermes Runs API. It is disabled by default and does not replace
the personal assistant's existing MCP connection.

## Workflow

1. Delegate an issue to **Квай-Гон Джинн**, or mention him in an issue comment.
2. The signed event enters a durable queue. A static status acknowledges the
   request; a resumable Hermes run handles the task.
3. If information is missing, a native `elicitation` activity asks a question
   and mentions the configured owner in the comment thread. Linear creates the
   comment and marks the session as awaiting input. Reply there to resume that
   exact run. Repeated polling or delivery retries do not post the question twice.
   Later follow-ups use the same task session.
4. Qui-Gon reads the live `linear_agents` roster and can create up to four
   sub-issues for **Dexter Jettster** (product/market/customer research) or
   **Лэндо Калриссиан**, slug `marketing` (promotion/SEO/content/experiments).
   Only registered, installed targets allowed by policy can receive work.
   You can also create a sub-issue under a known Qui-Gon task and delegate it
   to an installed specialist yourself. The child must be created by the configured
   owner or coordinator, in an allowed team. Specialists cannot delegate further.
5. Each specialist publishes its result in its own session. Once children finish,
   Qui-Gon resumes, reviews the findings and returns a combined answer in the
   parent session. Human assignees and issue workflow states are preserved.

Only the explicitly pinned human Linear user can start or continue work.
Usernames, issue text and mentions cannot grant permissions. Agent-created child
sessions also require a durable delegation record and verified parent lineage.
Results are visible to everyone who can read their Linear issue: these executors
receive no personal Telegram history, owner memory or personal MCP grants.
They have public web research, clarification, reviewed profile-local skills and
narrow task-bound tools. `council_skill` reads only those skill instructions and
Markdown references; it cannot execute scripts, edit skills or read credentials.
With `linear-task-management-v1`, `linear_issues` lets Qui-Gon read, search,
create and edit ordinary issues in the explicitly allowed teams, write comments,
set priority, project, human assignee, labels and due date, and manage workflow
states. New ordinary issues default to Backlog and have no delegate or automatic
execution. Use `linear_delegate` for actual specialist execution. Specialists can
read, comment, start and complete only their own authorized issue. No additional
OAuth grant is needed: the existing app already has read/write scope.

Delegated issues enter the team's first `started` state when execution begins;
a comment-only mention does not move an unrelated issue. Agents own routine
closure through `complete`, which requires a result and verification and refuses
open child issues. Done is distinct from a completed agent session. A blocking
decision uses resumable `clarify` with an owner mention; every unfinished action
must identify its next step and responsible person. Failures are not completion.
Writes have durable request keys. Create/comment retries reconcile stable UUIDs;
an uncertain update is read back and never blindly overwrites a later human edit.
Reference IDs come from the team's actual metadata, including renamed statuses.
Coding, terminal execution, private analytics and publishing are out of scope.
The coordinator supplies a bounded brief and reviews results. Sequential work
passes reviewed research to marketing; independent deliverables can run in parallel.

## Install

Each visible agent needs a separate private OAuth application, installed by a
Linear workspace administrator. An executor profile alone is not a Linear agent.
Use real immutable IDs from the intended workspace, not names or email addresses.

On the Docker host, after deploying a release with `linear-agents-v1`:

```bash
sudo python3 scripts/manage.py prepare-linear-agents \
  --owner HUMAN_USER_UUID --organization WORKSPACE_UUID \
  --team TEAM_UUID --with-dexter
```

The command snapshots and checks a copied profile, creates separate profiles and
starts only the optional services alongside the existing deployment. It copies
the model *selection*, never model credentials. It prints prefilled application
creation links. Review and create both applications in Linear. Their manifests
include unique HTTPS callbacks and signed webhooks for `AgentSessionEvent`,
`OAuthAuthorization` and `PermissionChange`. The OAuth installation requests
`actor=app` and scopes `read,write,app:assignable,app:mentionable`.

Enter each application's Client ID, client secret and webhook signing secret
through a private terminal, from the local checkout:

```bash
bash scripts/setup-linear.sh
bash scripts/setup-model.sh linear-executor
bash scripts/setup-model.sh dexter-executor
```

Authorize each executor's own model login. Do not reuse or copy the personal
assistant's token files. The existing `configure-route` command applies the
additive `/integrations/linear/` Nginx location with syntax checks, protected
service verification and graceful reload. Its default upstream is loopback
9121; if changing `COUNCIL_LINEAR_HTTP_PORT`, review that template too.

### Add Lando to an existing installation

Deploy a release with `agent-specialists-v1`, then repeat preparation with the
same immutable owner/workspace/team IDs and both `--with-dexter --with-marketing`.
Preparation allows additions and refuses identity changes or removals. It retains
existing executor credentials, custom instructions and integration data.

Create Lando's separate private app from the printed `marketing` manifest.
Enter only its new credentials with `bash scripts/setup-linear.sh marketing`.
This additive command preserves existing app secrets, grants, execution state
and policy epoch; it cannot rotate an existing app. It permits Qui-Gon to use
the newly registered specialist, but delegation remains blocked until that
specialist's app is actually installed. Use
`bash scripts/setup-model.sh marketing-executor` for its independent model login,
then the short OAuth setup window to install its Linear app. Check `doctor`
and model readiness before assigning real work.

No default project, pilot, campaign or recurring task is created. Assign work
through the tracker. GitHub writes, analytics accounts, Notion filing and
publication require separately scoped integrations; skills do not grant access.

### Install applications

On the host, open the short setup window:

```bash
sudo python3 scripts/manage.py configure-route
sudo python3 scripts/manage.py connect-linear-agents
```

Open the printed URLs and review each app installation in the intended
workspace. The callback verifies the workspace and stores renewable app grants.
PKCE, a single-use state and a browser-bound cookie protect the callback. No
codes or tokens enter access logs. Then enable execution:

```bash
sudo python3 scripts/manage.py linear-execution --enable
sudo python3 scripts/manage.py doctor
```

Use a real intended task to verify delegation, clarification, owner continuation
and child-result aggregation. Do not create test issues in a production team.

## Runtime and recovery

`linear-adapter` owns the queue, OAuth grants, outbox and delegation ledger. Its
only public upstream is loopback 9121; executor tool RPC uses unpublished 9122.
`linear-executor` and `dexter-executor` each mount only their own profile and
workspace. Authenticated Hermes APIs remain on the separate Docker network.
Containers run without root or added capabilities, with read-only root filesystems,
bounded CPU/memory/processes and rotated local logs. No Docker socket is mounted.

Persistent state lives under `$HERMES_DATA_DIR/.council/linear/`:

| Path | Contents |
| --- | --- |
| `bridge/` | Private registry, machine keys, app grants, SQLite ledger |
| `qui-gon/` | Coordinator config, own model login, session state, workspace |
| `dexter/` | Specialist config, own model login, session state, workspace |

Generic starting instructions are in [`instructions/linear/`](../instructions/linear/).
Both roles use brief Linear updates: a one-sentence status, a focused question
when blocked, and 3–5 result bullets with essential sources. Detailed answers
are given on request.
Preparation installs them once as each executor's `SOUL.md`; later profile edits
are preserved. These generic profiles do not participate in private skill sync.
All persistent Linear data enters the existing encrypted R2 backup. Snapshots
pause admission and freeze every owned writer; restored copies verify registry,
tool policy and database integrity. Caches, logs and runtime markers stay excluded
from portable backups. Recovery restores the recorded optional service set.
An installed Linear profile prevents rollback to an image without this capability.

Webhook replay, uncertain issue/activity writes and lost run receipts use stable
IDs. The adapter never blindly repeats model work after unknown execution.
An ambiguous clarification receipt stops the run and requests human review.
Unsupported approval prompts are rejected. Cancelling a coordinator also stops
its children through lineage revalidation. A stopped session cannot restart:
start a new Linear agent session for new work. Individual runs are limited to
30 minutes, clarification waits to 24 hours, and admission to 60 events/hour.
Completed webhook records remain seven days; avoid replaying historic events.

To stop execution without restarting the personal assistant:

```bash
sudo python3 scripts/manage.py linear-execution --disable
```

This advances the policy revision, rejects new admission and cancels old work on
the next worker sweep. Enabling starts a new admission epoch, so delayed events
created before activation cannot start old work. Revoke app installations in
Linear when removing access.
Keep execution disabled while changing application identities; installed app IDs
and grants must be deliberately reconciled before reinstallation. If maintenance
crashes, admission stays closed until a successful guarded startup clears the
maintenance marker. Inspect `doctor` and active jobs before manual intervention.

## Validate

`make test lint` covers owner checks, OAuth binding and rotation, replay races,
clarification, stops, policy reload, lost responses and both sub-issue flows.
`make test-integration test-linear` boots the pinned image offline with disposable
profiles, checks private API authentication and exact tool inventory, and proves
durable Runs API admission. No production credentials are used.
