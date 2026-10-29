# Technical review — 2026-10-29

Scope: this repository's deployment code, Telegram owner authorization, CI,
contributor workflow and production isolation. This is a code/operational review,
not a complete audit of Hermes, its Python dependency graph or the host OS.

## Findings addressed in this change

| Priority | Finding | Resolution |
| --- | --- | --- |
| High | Malformed owner settings could raise during provider registration and lose the intended auth provider | Always register a closed provider; reject login/session verification until configuration is valid; regression test against real Hermes |
| High | A fork inherited the repository owner's username | Remove all implicit owner defaults; require explicit owner settings |
| Medium | Compose interpolation could change secrets containing dollar signs | Use raw env files (Compose 2.30+), atomic private writes and a migration with a private backup; verify the actual container environment using fake credentials |
| Medium | Updating credentials could discard the numeric owner restriction | Preserve existing owner settings and validate interactive changes |
| Medium | Certificate renewal used another application's Certbot environment | Add a separate pinned maintenance container and a renewal lock; reload Nginx only after a certificate changes |
| Medium | Tests did not exercise cryptographic verification in the deployed adapter | Add real RSA signatures and pinned Hermes/PyJWT integration tests without network access |
| Medium | An edge rejection could hide the result of application auth tests | Add explicit origin routing with unchanged TLS hostname verification; require API 401 and exact provider status |
| Low | Contribution, security disclosure and maintenance guidance were absent | Add contribution/security/conduct policies, issue and PR templates, CODEOWNERS, changelog, architecture and dependency update configuration |
| Low | CI did not validate shell/workflow behavior or supported Python versions | Add ShellCheck, actionlint, Python 3.11/3.14, pinned action commits and Docker/Compose contract checks |

The runtime keeps loopback-only port publication, a dedicated bridge, CPU/RAM/PID
limits, bounded logs, limited bootstrap capabilities and an unprivileged agent
process. Its image contains neither production credentials nor agent state.
No existing application directory or Docker socket is mounted into Hermes.

## Validation

- Local suite: 18 unit tests pass; 5 live tests are opt-in and skipped locally.
- Origin suite: all 23 tests pass with the public hostname and origin routing;
  checks include private APIs, forged credentials, WebSocket access and setup-page
  HTML encoding.
- All 9 pinned-image tests pass, using generated RSA keys to verify owner enrollment,
  wrong signature/issuer/audience, expiry, unauthorized identities, per-request
  owner checks, missing/malformed settings and numeric-ID-only configuration.
- A real Compose container receives fake secrets containing dollar signs,
  quotes, backslashes and hash characters unchanged.
- Certbot's test renewal succeeds for both the custom and legacy hostname using
  the separate maintenance image; the active production certificates are retained.
- Ruff, Bash syntax, ShellCheck, actionlint and Dockerfile checks are required
  before merge; GitHub Actions repeats the portable checks without production
  credentials.
- Production isolation is checked against the retained Nginx checksums, the
  existing application's PID/start time and its HTTPS response.

Runtime tests do not prove that a real Telegram login or model request works.
Those require the owner's Telegram Login client and personal model-account
authorization. Neither has been completed as part of these automated tests.

## Remaining release and platform work

1. **Select a source license.** The repository is public but has no project
   license yet. The owner must choose before calling it an open-source release
   or accepting external code under a defined license. Third-party components
   retain their own license requirements.
2. **Keep the single-owner scope explicit.** The agent and dashboard share a
   trust boundary. A malicious plugin or an operator using agent terminal tools
   can modify the profile. Before supporting untrusted users/agents, introduce
   an independent auth/control plane, per-agent credentials/storage and execution
   isolation. Container resource limits alone do not provide tenant isolation.
3. **Choose the identity architecture.** Generic OIDC with an optional shared
   identity broker is proposed in [authentication-options.md](authentication-options.md).
   Shared credentials must not be distributed to third-party containers.
4. **Implement and test off-server recovery.** Local migration backups and a
   restart-persistence check exist; scheduled encrypted off-server backups and
   a complete recovery exercise are still needed.
5. **Prepare a portable release.** Current migration scripts target the existing
   server and preserve its legacy paths. A generic installation wizard, image
   publication/signing, SBOM/vulnerability policy and tagged releases remain
   future work. Historical native scripts are operational records, not a fresh
   installation workflow.
6. **Set repository merge policy.** Main-branch protection should require the
   final CI check names and disallow force-pushes, with an explicit maintainer
   recovery policy. CODEOWNERS alone does not enforce approvals.

Private vulnerability reporting, GitHub secret scanning and push protection are
enabled. These controls complement code review and do not replace checking the
staged diff and keeping state/secrets outside Git.

References: [Compose raw env files](https://docs.docker.com/reference/compose-file/services/#format),
[Telegram Login](https://core.telegram.org/bots/telegram-login),
[Hermes Docker](https://hermes-agent.nousresearch.com/docs/user-guide/docker),
[GitHub licensing guidance](https://docs.github.com/en/communities/setting-up-your-project-for-healthy-contributions/adding-a-license-to-a-repository),
[OpenSSF Scorecard](https://openssf.org/projects/scorecard/).
