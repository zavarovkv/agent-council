# Technical review — 2026-10-30

Scope: repository architecture, Docker isolation, deployment scripts, Telegram
authorization, state persistence, recovery and open-source maintenance. This is
not a complete audit of upstream Hermes, all dependencies or the host OS.

See [the architecture review](architecture-review.md) for prioritized follow-up
work, usability improvements and a scaling sequence.

## Findings and changes

| Finding | Change |
| --- | --- |
| Deployment relied on an operator script outside Git | Versioned install/deploy/recover CLI, documented prerequisites and CI checks |
| Old-image rollback could retain newly migrated data | Consistent checkpoint; test old/candidate copies offline; restore data, auth, image and Compose together |
| Updating Git changed the old release's Compose source | Record active Compose outside the checkout; explicit legacy-release adoption |
| Mutable tags obscured running provenance | Git-SHA image tags, reuse instead of overwrite, OCI revision/license labels and exact checkpoint image IDs |
| Runtime data could default inside Git | Require explicit external paths; reject overlaps and symlinks; disable implicit directory creation |
| Health could pass without a ready gateway | Read-only supervisor/fresh-state checks, protected API rejection and exact provider status |
| Account changes could race maintenance | Shared lock, private atomic writes and auth restart rollback |
| Off-host recovery lacked a standard workflow | Private R2/restic integration, independent password, actual download/offline verification before retention |
| Reproducible software inflated cloud copies | Data-only cloud checkpoints exclude images, tools, environments, caches, logs and nested backups; local rollback copies remain complete |
| Historical migrations looked like update commands | Explicit legacy opt-in, guarded builds and current operator documentation |
| No project source license | MIT selected by the maintainer; retain third-party licenses |
| Telegram reception, admission, policy, RPC and delivery were coupled | Separate modules and shared delivery lifecycle; independent public/business transports |
| Worker transport, history and model composition lived in one module | Strict protocol, bounded HTTP transport, transactional history and narrow model adapter |
| Queued work could be cancelled while waiting for capacity yet still call the model | Recheck eligibility after acquiring capacity; release pending claims on task cancellation |
| Local help replies attempted model-history acknowledgements | Acknowledge only generated replies after confirmed Telegram delivery |
| Equal timestamps could reorder model context | Stable ordering by creation time and insertion order; concurrent reservation regression tests |
| Explicit owner-ID setup did not publish an owner pin | Verified enrollment pins explicit IDs too; mismatched/corrupt existing pins fail closed |
| Existing session-file reads could follow symlinks | Reject unexpected file type, owner or permissions on lookup and revocation |
| Model/Drive setup could race maintenance; Drive assumed one Docker network | Shared operations lock and explicit selection of the private Hermes callback network |
| Restoring a checkpoint without a secretary could retain a newer machine-key file | Restore service credentials together with the checkpoint's profile and Compose source |
| Instructions were scattered and common reply policy was embedded in Python | Indexed `instructions/` tree included in the image; explicit provisioning preserves edited private profiles |
| Optional-route failures were hard to inspect safely | Read-only doctor reports route policy, worker health and whitelisted queue counts without private text or keys |

Earlier auth controls remain: no implicit owner, immutable numeric-ID pinning,
closed registration for invalid settings, raw literal credentials, browser-bound
single-use nonces, real JWT verification, revocable persistent sessions and
per-request invitation checks. Beauty.css remains an unchanged exact dependency.

## Boundaries and verification

Gateway/agents run as the dedicated non-root Hermes user. Root s6 bootstrap and
a writable container root are upstream exceptions, with limited capabilities.
CPU/memory/PID/log limits, two dedicated mounts and a loopback port remain in
place. No Docker socket or unrelated application path is mounted. The operator
`doctor` checks actual runtime controls rather than only the template.

The host operations layer and R2 keys are outside the agent. An optional service
guard compares unrelated services' PID, configuration hashes and HTTPS responses
before/after maintenance; it never controls them. Cloud activation requires a
private bucket, bucket-scoped token, independently saved password and a successful
first recovery check. Versioned timer files alone do not activate backups.

Required checks are unit tests, Ruff, Bash syntax, ShellCheck, actionlint,
Dockerfile checks, real Compose startup and pinned-image adapter tests. These
exercise generated RSA/JWTs, unauthorized identities, revocation/persistence,
literal credentials and the official SDK popup/callback contract without real
accounts. Operations tests exercise actual directory swaps after a simulated
failed migration, tampering, privacy, exclusions and retention scope.
`make test-backups` runs real restic offline, rejects a wrong password and recovers
a real Hermes session database. Actual run results belong in the pull request;
fixture success is not proof of a real Telegram/model/R2 connection.

## Remaining limitations

- Owner and invited operators share credentials, history and execution. Untrusted
  users/plugins/agents require a separate control plane and execution isolation.
- Backups briefly stop Hermes. Idle checks are not a distributed drain; a racing
  message may be interrupted. External failure alerts need an operator destination.
- A compromised bucket writer can delete encrypted backups. Whole-bucket locks or
  arbitrary expiry policies need a separately tested compatible design.
- Cloud recovery rebuilds recorded software from Git and requires pinned registry
  sources. Optional local image archives support offline recovery. A replacement-host
  exercise remains a separate operational check.
- Provider grants expire independently of backups. External Google OAuth apps in
  Testing can have short-lived refresh grants.
- Signed image publication, SBOMs, tagged releases, enforced branch protection and
  a response SLA need a release policy. No GitHub deployment credentials or
  unattended upstream upgrades were added.

See [Docker build guidance](https://docs.docker.com/build/building/best-practices/),
[Compose production](https://docs.docker.com/compose/how-tos/production/),
[R2 S3 setup](https://developers.cloudflare.com/r2/get-started/s3/),
[restic documentation](https://restic.readthedocs.io/en/stable/) and
[backup operations](backups.md).
