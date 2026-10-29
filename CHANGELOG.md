# Changelog

## Unreleased

- Style the Council sign-in page with pinned Beauty.css, system fonts and light/dark themes.
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
