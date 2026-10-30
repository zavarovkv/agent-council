# Changelog

## Unreleased

- Add a native Telegram Business handler and a separate, tool-free secretary with its own model login, per-chat history, revocation checks and a durable reply queue.

- License Agent Council under MIT; preserve third-party licenses.
- Add versioned install, deployment, consistent backup and image/data rollback commands.
- Track the active Compose source outside Git and use Git-revision image tags with OCI labels.
- Add encrypted, data-only Cloudflare R2 backups with verified recovery and opt-in daily scheduling.
- Exclude regenerable tools, environments, caches and logs from cloud backups.
- Require external runtime directories and strengthen read-only gateway/auth readiness checks.
- Serialize account/access changes with maintenance and restore credentials after failed setup.

- Align header sign-in actions to the right with Beauty.css 0.4.0's `beauty-nav-end` helper, without local styling overrides.

- Use the official Telegram Login SDK with a browser-bound, single-use server nonce and existing Hermes sessions.
- Install Beauty.css as a locked npm build dependency and propose updates through Dependabot.
- Style the Council sign-in page with pinned Beauty.css, system fonts and light/dark themes.
- Move the Telegram-branded sign-in link to the header and remove the landing footer.
- Add invited Telegram IDs and simple list/add/remove commands with restart rollback.
- Add explicit Telegram owner configuration without a repository-owner fallback.
- Keep the authentication provider registered and closed for malformed settings.
- Preserve literal credentials and owner restrictions when updating setup.
- Add offline cryptographic adapter tests, ShellCheck, actionlint and Compose
  contract tests; validate supported Python versions in CI.
- Isolate certificate maintenance from the existing application's environment.
- Add contribution, conduct, security and review documentation and dependency
  update configuration.

## Initial deployment — 2026-10-29

- Deploy Hermes v0.22.5 in a digest-pinned Docker image with separate persistent
  storage, resource limits and owner-only Telegram OIDC.
- Activate council.zavarov.com with HTTPS and retain the previous URL as a redirect.
- Keep the existing TravelLine service independent and preserve migration backups.
