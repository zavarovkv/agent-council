# Repository Guidelines

## Project Structure & Module Organization

Agent Council is a platform for managing, coordinating and monitoring AI agents. Its first integration is an isolated Docker Compose deployment of Hermes alongside TravelLine Dashboard. `src/telegram_auth/` contains owner-only Telegram OIDC authorization, `tests/` contains authorization and opt-in HTTPS tests, `config/` contains infrastructure templates, and `scripts/` contains deployment and account setup commands. `README.md` documents Docker operations; `docs/native-deployment.md` records the previous native setup and rollback.

## Build, Test, and Development Commands

Local tests use Python 3.11+ and the standard library. Install `requirements-dev.txt` into `.venv` for the pinned Ruff formatter/linter. Run `make test`, `make lint RUFF=.venv/bin/ruff`, and `make format RUFF=.venv/bin/ruff`. `make test-integration` requires Docker and checks the real adapter offline plus literal Compose credentials. CI checks Bash with ShellCheck and workflows with actionlint. Compose 2.30+ is required for raw auth env files. `make inspect` reads the remote server. Hermes runs in its official Python 3.14 image. Keep image digests and workflow action commits pinned. Never print production Compose configuration because it includes resolved secrets; use `config --quiet`.

## Coding Style & Naming Conventions

Follow the selected language's standard conventions and keep indentation consistent within each file. Configure a formatter and linter when adding the language toolchain, and commit their configuration.

Use descriptive module, function, and variable names. Keep modules focused on one responsibility. Match existing naming patterns as the codebase develops, and avoid mixing unrelated cleanup with functional changes.

## Testing Guidelines

Use `unittest`. Authorization tests must cover an unauthorized account, immutable-ID pinning, username reassignment, corrupted state and concurrency. Live deployment tests are opt-in via `HERMES_LIVE_URL`; they must not use real credentials or modify application data. Verify the existing service's response, process and configuration checksums around routing changes.

For direct-origin tests, use `HERMES_LIVE_ORIGIN_IP` while retaining HTTPS hostname verification. Never accept an edge 403 as proof that the application auth gate works. Do not silently assign a default owner to a new installation. This deployment supports one trusted owner; separate auth and execution boundaries are required before supporting untrusted users or agents.

## Commit & Pull Request Guidelines

The canonical repository is `https://github.com/zavarovkv/agent-council`. Use concise, imperative commit subjects, such as `Add council configuration loader`, and keep each commit focused.

Pull requests should explain the problem, summarize the change, link relevant issues, and report validation performed. Include screenshots for interface changes. Document configuration changes and any new setup steps.

## Security & Configuration

Keep credentials and local environment files out of version control. Provide placeholder values in example configuration files and document required environment variables when configuration is introduced.

Never modify the existing TravelLine application, virtual environment, service or virtual host during Hermes work. Use the dedicated `hermes` account and keep its listening port on loopback. Preserve the owner-only auth gate, including while Telegram client credentials are absent. Enter secrets through the interactive setup script; never put secrets in chat, source files, shell command arguments or logs. Apply Nginx changes with syntax validation, an additive virtual host and graceful reload; retain a rollback path for only the new route.

Keep Docker data, model credentials, sessions, memories, generated skills and backups outside the Git checkout. Never mount the host Docker socket or existing application's directories into an agent container. Test a copied profile before cutover; retain the native data and a consistent backup. Do not add automatic deployment credentials to GitHub Actions without a separate user request.
