# Contributing

Agent Council is an early-stage project. Its current implementation is a
single-owner Hermes deployment; it is not yet a multi-tenant agent platform.

For a substantial feature, open an issue describing the problem and proposed
behavior before implementing it. Keep changes focused and use descriptive,
imperative commit subjects. Small fixes can go straight to a pull request.

## Development

Use Python 3.11+ on Linux or macOS, Make and Bash. Create a virtual environment
and install `requirements-dev.txt`. Run `make test` and `make lint`; use
`make format` before submitting. Set `RUFF=.venv/bin/ruff` if necessary.

Docker integration tests require Docker and run with `make test-integration`.
They use the real pinned Hermes adapter, generated test signing keys and no
network. Live deployment tests are opt-in; never point tests at another
operator's deployment without permission. Never weaken an assertion to accept
a Cloudflare rejection as proof that application authentication works.

CI also runs ShellCheck, actionlint and a real Compose parsing test using fake
credentials. Do not add production credentials to CI. New dependencies and
container base updates need a reason, a pinned version/digest and validation.

## Pull requests

Explain the problem, resulting behavior and how it was verified. Include a
regression test for a bug when it protects meaningful behavior. For changes
to authentication, test both successful authorized access and rejected access.
For deployment changes, include compatibility, state migration and rollback
notes. Add screenshots for visible interface changes.

Do not commit `.env` files, private keys, OAuth tokens, session databases,
generated user skills, memories, logs or backups. Use placeholders in examples.
Check the staged diff before pushing. Disclose security issues through the
private process in [SECURITY.md](SECURITY.md), not a public issue.

Maintainers review and merge pull requests after CI passes. Contributors must
have the right to submit their changes. Before external code contributions
are accepted, the repository's license must be selected and published.
