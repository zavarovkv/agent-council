# Architecture review — 2026-10-30

Scope: first-party code, runtime boundaries, Telegram authorization and delivery,
Docker, host operations, instruction releases, backups, CI and the integration
contracts of pinned Hermes v0.22.5. This is a design/code/operations review, not
a complete audit of upstream dependencies or the host OS. Capacity has not been
established by a sustained load test. Deployment evidence belongs in PR #22.

A live non-load sample used about 515 MiB of the main runtime's 2 GiB limit and
119 MiB of the worker's 512 MiB limit, with about 38 GiB free on the host. This
sample supports monitoring and load testing as the next capacity step.

## Assessment

The current structure fits a personal, trusted deployment: one supervised Hermes
runtime, a separate tool-free correspondence worker and host-only maintenance.
The main improvements now are operational visibility, fair resource allocation,
reliable task/result workflows and a clear capability model before expanding
access. Multi-host orchestration becomes useful after those boundaries and
measured capacity requirements exist.

Keep the four data lifecycles explicit:

| Layer | Source of truth | Release/recovery |
| --- | --- | --- |
| Application and infrastructure | Public Git, pinned dependencies/images | Tested immutable code release |
| Personal instructions and custom skill source | Private Git; trusted `main` update channel | Reviewed SHA, offline copy validation, checkpoint, guarded activation |
| Conversations, memory, runs and artifacts | Persistent Hermes profiles/workspace | Consistent data backup and tested recovery |
| Credentials and recovery password | Root-only host files; independent recovery copy | Explicit provisioning/rotation, outside agent Git access |

The secretary receives its own instructions and provider grant. Its conversation
styles do not grant private tools or family identity. Invited dashboard operators
share the owner's execution environment; profiles and board filters are logical
organization inside that trusted environment.

## Improvements implemented in this pass

- Personal instructions can be installed from a protected private checkout. Only
  the host receives a read-only GitHub key; image builds and public CI remain
  independent of private source and credentials.
- Explicit SHA installation and five-minute `main` synchronization share the
  maintenance lock, drift checks, consistent checkpoint and copied-profile test.
  Conflicting runtime edits remain intact. Documentation-only revisions update
  release metadata without restarting services.
- Failed candidate validation/cutover quarantines that revision. Manual restore
  disables the update timer, preserving the restored release until an operator
  chooses to resume updates.
- Copied profiles validate managed skill metadata with Hermes' actual safe YAML
  loader. This avoids assuming an additional YAML package exists in the image.
- `doctor` reports private instruction revision/drift, automation state and the
  last verified cloud backup. It exposes no prompt text, identities or keys.
- Cloudflare R2 was initialized with bucket-scoped access. An encrypted upload,
  download and offline recovery check succeeded before daily scheduling was
  enabled. Source images/software/caches remain outside cloud data copies.
- CI tests PRs and main, cancels superseded runs and avoids duplicate branch-push
  and PR builds. Python 3.11/3.14, real pinned-image integration, browser contracts
  and backup recovery remain required checks.

These build on existing immutable owner pinning, verified Telegram JWT/nonces,
revocable persistent sessions, durable reply queues, resource limits, loopback
exposure and image/data rollback. See [technical review](technical-review.md).

## Prioritized follow-up work

### P1 — Shorten and explain maintenance windows

A user-facing 502 was observed during this maintenance. Deployment, private
instruction activation and consistent snapshots currently stop owned services;
profile copying, verification and gateway startup make repeated operations
noticeable in the web UI. This is an observed usability/availability issue.

Coalesce maintenance steps, validate staged instruction bundles before downtime
and separate style/skill activation from code-image deployment. Use atomic bundle
selection and a tested reader reload where native contracts permit it. Investigate
coordinated online SQLite/file backups before reducing stop-the-world snapshots;
consistency must remain explicit. Add a maintenance response with Retry-After on
the Council route and measure an availability budget. Preserve the single Telegram
poller and image/data rollback when designing any overlapping cutover.

### P1 — Protect business replies from public traffic

Both transports share two generation slots and the worker's global hourly
budget. A large amount of valid public traffic can delay selected account chats
or exhaust their shared budget. This is an availability risk inferred from
[`plugin.py`](../src/telegram_secretary/plugin.py),
[`delivery.py`](../src/telegram_secretary/delivery.py) and
[`history.py`](../src/secretary_worker/history.py); no production attack is asserted.

Add trusted route classification to the fixed RPC contract, reserved business
capacity/budgets and fair scheduling across chats. Preserve per-chat ordering,
deduplication and the uncertain-send policy. Verify with a fake slow provider:
public saturation must leave bounded business capacity, and cancellation must
continue to prevent unnecessary model calls. Record input/output usage when the
provider supplies it, and expose route budgets to the owner.

### P1 — Deliver failure and stale-backup alerts

Timers, exit codes and `doctor` provide local evidence. An operator still needs
an alert when credentials expire, a backup becomes stale, instruction activation
fails or the host becomes unreachable. The current backup unit retries, but no
external notification destination is configured.

Add one approved operator destination and an external reachability check. Dedup
alerts by failure/revision and keep messages free of private content. Monitor
backup verification age, failed update revision, queue age, disk space and
provider-auth failures. A staged failure should produce one useful alert and a
recovery notification, without leaking credentials or opening application APIs.

### P1 — Rehearse recovery on a replacement host

Actual R2 download and offline database checks are valuable evidence. A prepared
replacement-host exercise must also prove account/UID setup, pinned image rebuild,
Nginx/TLS provisioning, dashboard access and provider reauthorization when needed.
Measure recovery time and define the tolerated data-loss window with the owner.

Use [`recover`](../src/council_ops/install.py) and the recorded revision. Confirm
the restored private rules start without GitHub availability, recreate host-only
read access separately and resume timers deliberately. Never rehearse by swapping
the live data volume. See [backup recovery](backups.md).

### P1 before expanding privileges — Enforce a capability model

The spouse username selector is a style preference. The owner and invited web
operators share a runtime. Additional family/guest tool access requires immutable
actor IDs, explicit capabilities, scoped credentials and execution/storage
boundaries. These checks belong in the server/tool layer, independently of
editable skills.

Define a small matrix first: actor, agent, operation, resource scope, approval
requirement. Test denied tool calls and cross-profile data access. Untrusted full
agents require separate runtimes and grants. Preserve the existing tool-free
correspondence path while this work is designed. See [security](../SECURITY.md).

### P2 — Build research workflow around native Kanban

Pinned Hermes already supplies SQLite-backed boards, Task/Run/Event/Attachment
models, claims, review transitions and dashboard APIs. Use an adapter to those
contracts for task ownership, execution and review. A duplicate Council task
database would create two competing status sources.

The useful addition is a reliable filing workflow: stable task/run IDs on local
reports, a reviewed artifact record and an idempotent Notion/Drive outbox. Prompt
instructions about retries and parent-page indexing should be backed by code
where writes must survive lost responses and restarts. A filing retry must reuse
the same report and update one index row. Link saved artifacts from the native
task board and verify destinations through the configured cloud integration.

### P2 — Make operator state visible in one place

Expose a trusted operator view for each agent: profile, model/provider readiness,
installed instruction SHA, pending update/conflict, current task/run, last result
and backup age. Build on `doctor` and native Kanban APIs. Keep root maintenance
behind a narrow host operations boundary; a dashboard must not receive Docker
socket access or Git/R2 keys to implement these controls.

For portability, resolve deployment account, paths, container/service names and
origin from validated configuration. Several setup commands and unit templates
still document this specific Ubuntu host. Add a fixture installation with
non-default names/paths before calling the installer generic.

### P2 — Minimize anonymous status responses

An unauthenticated request through the public Cloudflare hostname returned HTTP
200 from `/api/status` with `profiles`, `memory`, `disk`, `install_id` and
`gateway_platforms` fields. Private configuration/session APIs still reject
anonymous requests; this finding concerns unnecessary operational metadata,
rather than demonstrated credential or conversation disclosure.

Keep only an explicit allowlist of login-bootstrap fields public. Require a
verified operator session for detailed diagnostics, with a separate container
health contract if needed. Preserve the Telegram login/bootstrap flow and test
the anonymous response schema through both the origin and the edge. The current
live check verifies the enabled auth provider but does not reject extra status
fields, so it needs a schema-boundary assertion when this is implemented.

### P2 — Add structured diagnostics and release discipline

Keep generic public errors, and record internal failure categories, run/request
correlation IDs, route, latency, queue age and retry/delivery outcome. Persist
enough to distinguish provider authentication, quota, Telegram permission and
ambiguous network delivery failures without storing raw exception bodies.

Publish versioned releases with a compatibility table for Hermes, Python, model
adapters and data schemas. Add SBOM/vulnerability review and a tested dependency
update cadence. Image signing/registry publication need an explicit release
policy. Keep public workflows free of deployment secrets.

Extend the compatibility suite to app downgrades containing private instruction
state, and to the actual Telegram adapter's menu-startup contract. The current
menu integration depends on pinned native startup attributes; a fixture adapter
alone cannot establish that an upstream update preserved those attributes.

## Scaling sequence

1. Finish operator visibility, alerts, route fairness and the research filing
   contract on the current host. Preserve native Hermes task/run semantics.
2. Introduce a small declarative agent catalog for model, profile, instruction
   release and allowed capabilities. Validate it and reconcile it through the
   existing host operations layer.
3. Separate the control API from execution before supporting untrusted agents
   or mutually untrusted operators. Assign per-agent volumes, provider grants,
   resource budgets and network policies.
4. Add additional worker hosts when measured concurrency/availability needs
   require them. Choose shared task/lease storage and a scheduler at that point;
   SQLite-backed local state and host-local locks currently assume one machine.

Keep runtime/provider upgrades separate from instruction releases. Use synthetic
conversation fixtures and opt-in model evaluations for changes in assistant
behavior, with an explicit evaluation budget and no real private chats in CI.

## Verification basis

Unit tests cover auth rejection/revocation, concurrent pinning/reservations,
delivery cancellation/ambiguity, private-file boundaries, actual offline Git
updates, no-op releases, drift preservation and rollback/update quarantine.
Integration tests exercise the pinned Hermes adapter, skill parser and isolated
service. Live checks verify origin auth independently of Cloudflare, provider
readiness without sending Telegram messages and the existing service guard.

This establishes tested contracts for the current deployment; multi-host load,
disaster recovery on a replacement host and future capability policies require
their own acceptance tests.

References: [Docker Compose production](https://docs.docker.com/compose/how-tos/production/),
[GitHub workflow concurrency](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/control-workflow-concurrency),
[restic repository operations](https://restic.readthedocs.io/en/stable/045_working_with_repos.html).
