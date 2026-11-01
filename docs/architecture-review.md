# Architecture review – 2026-11-01

Scope: first-party runtime, skills and release tooling; Telegram, Linear, Notion,
Drive, public landing, Docker, backups and live operational state. This is not a
pentest of upstream Hermes, the whole host or cloud providers. No production
messages, model requests or document edits were needed for the review.

## Decision

Keep Hermes, Linear and single-host Docker Compose. Their responsibilities are
appropriate for four agents and the measured load. Consolidate contracts and
remove conflicting policy copies before adding more infrastructure. The main
architectural risks are ambiguous writes, gaps between instructions and enforced
permissions, and missing behavioral/capacity evidence.

[Current component and trust diagram](architecture.md) is the operating model.
The blog owns public landing source and automatic static deployment. Linear is
the only task tracker. Notion owns formatted text deliverables; Drive owns shared
files. Personal runtime memory stays local; the Drive memory-sync proposal
(PR #59) remains closed and deferred.

## Evidence and corrections

| Priority | Finding | Change and evidence |
| --- | --- | --- |
| P1 | Notion ancestry parsing searched page content as well as metadata; a parent-like tag in prose could cross the configured subtree boundary | Only the envelope before content establishes ancestry; regression tests reject forged read/create scope |
| P1 | A preflight validation error was recorded as an attempted cloud mutation, permanently blocking the same request key | Read-only preparation precedes atomic reservation; tests cover corrected retry, offline confirmed replay, competing callers and uncertain mutation refusal |
| P1 | Notion readback removed punctuation, so changed signs, decimals and URL paths could be reported as verified | Content comparison retains punctuation and targets while allowing known Notion escaping; negative regression cases added |
| P1 | Drive upload verification compared name and size only | Verify the provider's content checksum too; missing or different checksum cannot produce `verified: true` |
| P2 | Workspace context still said only the owner can close tasks, contradicting the coordinator's scoped acceptance tool | Storage context delegates task transitions to the Linear workflow; storage no longer defines acceptance policy |
| P2 | Isolated skill discovery used regex while Hermes parsed YAML; folded descriptions became a literal `>` or `|` | One runtime metadata contract serves discovery and pre-activation verification; actual pinned-image tests cover quoted, folded, literal and unsafe YAML |
| P2 | Private CI accepted malformed metadata and did not enforce runtime name/size limits | Safe YAML decoding, directory/name/type/size checks and real-parser tests; the secretary's three historical route aliases remain explicit |
| P2 | Architecture/README described two services, native Kanban as the next tracker, stateless-only guest calls and no static deploy identity | Replaced with current services, source-of-truth and trust boundaries; public example instructions are distinguished from installed private policy |

The skills change is to validation and documentation. Agent roles, model choices,
private instruction wording and permissions are preserved. Existing durable-write
receipts keep their schema and meaning; old uncertain mutations are not reclassified
as safe to replay.

## Live state sampled before changes

- Seven running services: main Hermes, secretary, Linear adapter, four executors.
  Doctor reported healthy containers, loopback-only published ports, verified
  mounts and a non-root gateway.
- Four Linear applications installed; execution enabled; 37 completed bridge jobs,
  one cancelled job and no pending outbox activity in that sample.
- Private instruction revision `7a01a67ed2cdb3219024ac4c50ec1625bb854e99`:
  122 managed artifacts, no local drift, active synchronization timer.
- Backup timer active; its last recorded verified recovery was about 2.5 hours old.
  This is existing recovery evidence, not a new replacement-server exercise.
- About 40 GiB free on the 75 GiB disk. Main Hermes used about 655 MiB of 2 GiB;
  executors about 264–269 MiB of 768 MiB each; secretary 119 MiB of 512 MiB;
  bridge 18 MiB of 256 MiB. These are idle/light-load samples, not load-test limits.
- Docker reported 5.1 GB of image storage and 5.1 GB of build cache. Shared layers
  mean these totals must not be naively added. No disk cleanup was necessary.

## Remaining engineering work, in order

### 1. Global task admission and proactive coordination

Each isolated executor currently defaults to one concurrent native run. The
bridge also caps each parent at four unfinished specialist delegations. Neither
is a global five-task limit across Telegram, dashboard, cron and Linear. An
instruction saying “at most five” does not supply a scheduler.

Implement a small admission module in the existing platform, with durable leases,
per-executor and total budgets, idempotent acquisition, heartbeat/expiry and a
queue. Every execution entry point must participate before the limit is described
as global. Reserve coordinator capacity to avoid a parent waiting on children
while occupying the last slot. Define whether awaiting-input runs consume a
slot; test restart recovery and starvation. Do not add a second backlog.

Only then enable a bounded periodic coordinator sweep of Linear: prioritize,
identify blocked work, continue ready tasks, request a missing decision once, and
report exceptions. A skill file does not install this schedule. Keep planned
Гален/K-2SO roles unavailable until their executors, grants, tools and evaluation
are provisioned. Existing AGE-27 tracks this work; do not duplicate it.

Acceptance evidence: simultaneous admission from every supported entry point
never exceeds the configured budget; retries/restarts do not duplicate a run;
waiting parents cannot starve their children; revoked work stops before effects.

### 2. Skill behavior and concise reporting

The private repository already has role sources, generated adapters, shared
procedures and an evaluation-case catalog. Static CI checks do not execute those
cases. Build a disposable evaluation runner pinned to runtime, model and skill
SHAs, with fake tools/data, fixed budget and independently reviewed traces.
Compare baseline/candidate: role selection, required editorial handoff, questions,
status transitions, actual evidence, denied actions, tokens and elapsed time.

The Linear tool limits ordinary comments to 700 characters and review result/check
fields to 500/160. The bridge's final AgentActivity response can still contain up
to 16,000 characters. This is a separate path and explains why a short-comment
skill alone cannot guarantee concise updates. Add an explicit deliverable/summary
contract before enforcing a final-response limit: preserve the full artifact in
Notion and publish a short summary plus verified link. Do not silently truncate
research and claim the artifact was saved.

Acceptance evidence: a long-output fixture is preserved as an artifact, the
published update stays within the agreed limit, retries do not duplicate either,
and missing artifact access produces a clear actionable failure.

### 3. Concurrent editing and uncertain-write reconciliation

Current Notion and Drive revision checks prevent knowingly replacing an already
stale version, but do not make the provider mutation atomic. Notion full-content
replacement can race another editor. Use versioned deliverables and explicitly
promote the accepted version; investigate provider-supported conditional updates
before promising compare-and-swap guarantees. The workspace lock only serializes
this service, not human or external API edits.

Unknown writes currently fail closed. Add an operator reconciliation command
which inspects candidate objects/receipts and records a confirmed outcome, with
an audit record. It must not clear pending rows or issue a new create blindly.
Consider finer resource locks only after measuring the current serialized service;
keep credential refresh and same-document writes serialized.

### 4. Operational visibility and recovery objectives

Extend existing `doctor`/monitoring with safe error categories, queue age, run
correlation and latency, not transcripts or raw provider exceptions. Distinguish
healthy process, ready provider grant and successful task. Expose instruction
revision and available capabilities in the trusted operator view.

Agree on recovery point/time objectives and rehearse a replacement host including
DNS/TLS and provider reauthorization. CI fixture recovery and an R2 restore check
are useful but do not prove those external steps. Retain data-only backups and
root-only recovery keys. Add dependency/SBOM review to the existing pinned update
process before considering a signed release registry.

### 5. Scale when measurements require it

Seven containers and the observed headroom do not justify Kubernetes, a message
broker or splitting every Python module into a network service. First measure
concurrent task throughput, provider rate limits and recovery behavior. SQLite
and local locks are appropriate to the current single-host design; multi-host
workers would need shared lease/queue storage and a separate execution trust
boundary. Full untrusted agents require stronger per-tenant isolation first.

## Verification

Baseline: 309 unit tests, seven opt-in live tests skipped. Candidate: 317 unit
tests, the same seven skips; Ruff and shell/workflow checks run separately.
Private baseline: 67 named skill instances plus policy/generated-file checks and
eight trace-validator tests. The candidate adds two real-YAML tests. Real skill
behavior has not been evaluated and is not reported as passing.

Pinned-image integration and deployment receipts must be checked on the reviewed
commit before cutover. Live verification must retain TLS hostname validation,
check the origin authorization gate, inspect all four executor catalogs and
confirm TravelLine process/configuration checksums. Cloudflare 403 alone is not
a successful application-auth test.

## Primary references

- [Hermes profiles](https://hermes-agent.nousresearch.com/docs/user-guide/profiles):
  separate profile state and configuration; this is not container isolation.
- [Hermes skills](https://hermes-agent.nousresearch.com/docs/user-guide/features/skills)
  and [Agent Skills format](https://agentskills.io/specification): on-demand
  procedures with structured metadata; use actual safe YAML semantics.
- [Linear agent interaction](https://linear.app/developers/agent-interaction) and
  [webhooks](https://linear.app/developers/webhooks): session lifecycle, signed
  event delivery and separate issue workflow.
- [Drive file resource](https://developers.google.com/workspace/drive/api/reference/rest/v3/files):
  file version and binary-content checksum are different properties.
- [Docker Compose production](https://docs.docker.com/compose/how-tos/production/):
  production configuration and service-level deployment on a single host.

Latest online documentation is guidance, not proof of a pinned runtime contract;
the offline image integration suite checks the version actually deployed.
