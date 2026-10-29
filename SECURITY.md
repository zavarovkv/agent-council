# Security policy

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

This is currently a **single trusted owner** deployment. Agent terminal tools
can execute code, and user-installed plugins are executable code. Treat model
access, skills, plugins and the writable agent profile as trusted operator
capabilities. The dashboard and agent execute in the same container/process
trust boundary; this is not a hardened boundary against a malicious plugin.

Do not expose it to untrusted users or use one shared profile for mutually
untrusted agents. A future multi-user platform needs a separate authentication
gateway/control plane and per-agent credentials, storage and execution sandboxes.
Protecting the dashboard from hostile code already running inside its own
runtime is outside the current supported threat model.

## Deployment controls

- Keep the application port on host loopback behind HTTPS Nginx.
- Keep owner authorization enabled, including during initial setup. Prefer an
  explicit numeric Telegram ID in the root-owned authentication environment.
  Username-based enrollment is a bootstrap convenience; later ID pinning is
  stored in the trusted owner profile.
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

Sessions, memories, skills and model OAuth credentials persist in the private
data volume. Include them in a consistent backup, preferably encrypted off-server,
and test recovery. A container restart test is not disaster-recovery validation.
Stop Hermes while copying SQLite files. Back up state before an image/schema
upgrade; an older image may not understand a newer schema.

Use the interactive setup command to rotate Telegram credentials. It preserves
the configured owner restriction. To revoke a model account, use Hermes' account
management commands and revoke the session at the provider when appropriate.
Never include auth files or raw logs in a support bundle without reviewing them.
