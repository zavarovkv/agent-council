# Security policy

Interactive Telegram logins use a revocable, random application session lasting
30 days by default (configurable 1–90). The short-lived Telegram token is fully
verified at login and is not retained. Only session-token hashes and verified
identity metadata persist in the private profile database. Every request checks
the current owner/invitation policy; logout removes the session record. Keep the
profile database private and outside Git, and include it only in protected backups.

## Reporting

Use [GitHub's private vulnerability reporting form](https://github.com/zavarovkv/agent-council/security/advisories/new).
Private vulnerability reporting is enabled for this repository. Include the
affected revision, reproduction steps, impact and a minimal sanitized example.
Do not post exploit details or credentials in public issues. Response times
are best effort; this project does not promise a security support SLA.

Only the current main branch and its pinned dependency set are maintained.
Older deployment snapshots are retained for operator rollback, not security
support. Review container and dependency updates before applying them.

## Supported threat model

This is currently a **trusted owner and explicitly invited operators** deployment.
All allowed accounts share one Hermes runtime, history, tools and permissions.
There are no per-user roles or private user workspaces. Agent terminal tools
can execute code, and user-installed plugins are executable code. Treat model
access, skills, plugins and the writable agent profile as trusted operator
capabilities. The dashboard and agent execute in the same container/process
trust boundary; this is not a hardened boundary against a malicious plugin.

Do not invite untrusted users or use one shared profile for mutually
untrusted agents. A future multi-user platform needs a separate authentication
gateway/control plane and per-agent credentials, storage and execution sandboxes.
Protecting the dashboard from hostile code already running inside its own
runtime is outside the current supported threat model.

The optional Telegram secretary is a separate, deliberately limited boundary:
it handles text from the pinned owner's selected business chats in an isolated
tool-free service, with its own model grant/history and no private-agent mounts.
Business updates never enter the owner's command/tool dispatcher. This does not
authorize general guest execution or access to private Notion/Drive integrations.
See [secretary setup and delivery limits](docs/secretary.md).

## Deployment controls

- Keep the application port on host loopback behind HTTPS Nginx.
- Keep owner authorization enabled, including during initial setup. Prefer an
  explicit numeric Telegram ID in the root-owned authentication environment.
  Username-based enrollment is a bootstrap convenience; later ID pinning is
  stored in the trusted owner profile.
- Add collaborators only by numeric Telegram ID in the root-owned allowlist.
  Removing an invitation and recreating Hermes rejects the removed account's
  existing tokens and disconnects open sessions. The separate owner setting
  must be changed separately to replace/revoke the owner.
- Keep credentials in a root-only file outside Git. Compose uses raw env-file
  parsing so interpolation cannot silently alter a secret.
- Never mount the host Docker socket, root SSH keys or unrelated applications
  into the agent container. Preserve the capability, CPU, memory and PID limits.
- Do not pass production secrets to pull-request workflows. Do not print
  resolved production Compose configuration.
- Keep Cloudflare-to-origin TLS encrypted, with certificate validation enabled.
  Browser Integrity Check may reject automated clients with error 1010; test
  origin authentication independently instead of disabling protection globally.

## State and recovery

The official Telegram popup result is verified server-side. Widget bootstrap
POSTs require the configured HTTPS origin and JSON content. A single-use,
short-lived challenge binds the signed nonce to an HttpOnly/Secure/__Host cookie.
Challenge memory and per-client issue rates are bounded. JWT signature,
issuer, audience, expiry and nonce are checked before owner enrollment. Widget
tokens are posted in request bodies, not URLs or browser storage. Session cookies
reuse Hermes' hardened helpers. The ordinary OIDC/PKCE flow is the SDK-failure fallback.

Sessions, memories, skills and model OAuth credentials persist in the private
data volume. Include them in a consistent backup, preferably encrypted off-server,
and test recovery. A container restart test is not disaster-recovery validation.
Stop Hermes while copying SQLite files. Back up state before an image/schema
upgrade; an older image may not understand a newer schema.

Use the versioned operator commands in [deployment](docs/deployment.md) and
[backup/recovery](docs/backups.md). Cloud copies contain selected persistent data
encrypted by restic. R2 credentials and the separate recovery password are root-only
and are never mounted into Hermes. The writer key can delete backups; encryption
does not provide deletion protection. Keep the password outside the primary host
and monitor failed backup units. Whole-bucket locks/lifecycle expiry are incompatible
with an unmodified restic retention policy.

Use the interactive setup command to rotate Telegram credentials. It preserves
the configured owner restriction. To revoke a model account, use Hermes' account
management commands and revoke the session at the provider when appropriate.
Never include auth files or raw logs in a support bundle without reviewing them.
