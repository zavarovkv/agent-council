# Agent Council

A platform for managing, coordinating, and monitoring AI agents.

The first integration is a self-hosted Hermes Agent with a web dashboard,
owner-only Telegram authentication, persistent storage and Docker Compose.
Code and infrastructure are versioned in
[zavarovkv/agent-council](https://github.com/zavarovkv/agent-council).

## Deployment

Prerequisites: Linux, Docker Engine 29+, Docker Compose v2.24+, Bash, OpenSSH
and Python 3 for setup scripts. The current server has Engine 29.9.2 and Compose
5.6.1. The official Hermes **v0.22.5** base image is pinned by SHA-256.

`Dockerfile` adds the owner policy as immutable application code. `compose.yaml`
limits Hermes to one CPU, 2 GiB RAM, 256 processes, bounded logs, a dedicated
bridge network and a loopback port. No host Docker socket or TravelLine
directories are mounted. The official image starts s6 as root and drops the
actual Hermes process to the configured unprivileged UID/GID.

| Item | Server location |
| --- | --- |
| Deployment checkout | `/opt/agent-council` |
| Compose settings | `/etc/agent-council/compose.env` |
| Telegram Client ID / Secret | `/etc/hermes/telegram.env`, root-only |
| Sessions, memory, skills, model login | `/var/lib/agent-council/hermes` → `/opt/data` |
| Agent workspace | `/var/lib/agent-council/workspace` → `/workspace` |
| Nginx virtual host | `/etc/nginx/sites-available/hermes` |
| Separate HTTPS certificates | `/etc/hermes/letsencrypt` |
| Native profile backup | `/opt/hermes-deploy/docker-migration/hermes-native-before-docker.tar` |
| Retained native profile | `/var/lib/hermes/.hermes` |

GitHub stores source, templates and commit history. Credentials and live data
stay outside the checkout. Container recreation preserves the bind-mounted
state. This persistence is separate from an off-server disaster-recovery backup.

The existing TravelLine Dashboard at `https://2.29.13.129/` is independent.
Hermes is available at **https://council.zavarov.com/** through Cloudflare,
with a separate origin certificate. The initial `sslip.io` hostname redirects
to the custom domain.

## Initial migration

These scripts target the current Ubuntu server and are one-time operational
tools, not a generic installer or an update command:

1. `scripts/install-docker.sh` installs Docker from its signed official apt
   repository without upgrading unrelated packages.
2. Build with `bash scripts/compose-server.sh build` from the server checkout.
3. `scripts/stage-docker.sh` starts a copied profile on `127.0.0.1:19119`.
4. `scripts/migrate-to-docker.sh` stops only native Hermes, takes a consistent
   profile/workspace backup, starts Compose on `127.0.0.1:9119`, verifies data
   across a container restart and disables the old Hermes unit. A failed
   cutover restores native Hermes.

The migration refuses to overwrite an existing production data directory.
The stopped native profile is preserved. See
[the native deployment record](docs/native-deployment.md) for its setup details.

## Telegram sign-in

In `@BotFather`, choose or create a bot and open **Login Widget**. Register the
current public origin and its `/auth/callback` URL:

```text
https://council.zavarov.com
https://council.zavarov.com/auth/callback
```

From the project on your Mac, run:

```bash
bash scripts/setup-telegram.sh
```

Enter the Login Widget **Client ID** and **Client Secret** in the SSH terminal.
The secret is hidden and saved directly on the server. The Bot API token is
a different credential. The script recreates Hermes to load the environment,
checks its auth gate, then removes the setup-status page.

Only a verified **@zavarov** login can bootstrap access. Hermes verifies the
OIDC signature, issuer, audience and expiry before the owner policy pins the
numeric Telegram ID. Later logins require that ID even after a username change
or reassignment. Corrupted identity state denies access. Alternative dashboard
providers are disabled. Until configured, the dashboard remains locked.

## ChatGPT / Codex account

From the project on your Mac:

```bash
bash scripts/setup-model.sh
```

Select **ChatGPT or Codex Subscription**, open the OpenAI URL in your own
browser, confirm the code and choose an available model. The command runs
inside the container as Hermes; credentials persist in `/opt/data`, outside
GitHub. If device-code sign-in is disabled, enable it in ChatGPT security or
workspace settings, or use Hermes' browser flow. A real response can only be
verified after this user-controlled login.

## Operations

On the server:

```bash
cd /opt/agent-council
bash scripts/compose-server.sh ps
bash scripts/compose-server.sh logs --tail 100 hermes
bash scripts/compose-server.sh restart hermes
```

After reviewing and testing an update:

```bash
git pull --ff-only
bash scripts/compose-server.sh build
bash scripts/compose-server.sh up -d --wait
```

Back up the data directory, workspace and root-only configuration before
upgrades. Stop only Hermes while copying its data for consistent SQLite
backups, then start it again. Keep backups outside the checkout. To roll back
the initial migration, stop Compose and enable/start `hermes-dashboard.service`.
Back up any new Docker state before rollback to preserve post-cutover logins
and sessions.

## Custom domain

Cloudflare has `A council → 2.29.13.129` with proxying enabled. The HTTP-01
challenge works through the proxy, and the origin has a valid Let's Encrypt
certificate. Keep Cloudflare's connection to the origin encrypted; use Full
(strict) mode for this hostname. The one-time switch is recorded in
`scripts/activate-council.sh`, which has a configuration rollback. Both Nginx
virtual hosts keep their challenge routes for renewal. The existing TravelLine
virtual host remains unchanged.

## Development

`src/telegram_auth/` contains the owner policy; `config/` and `scripts/` contain
infrastructure and maintenance commands; `tests/` contains security checks;
`assets/` contains the setup page. Local tests require Python **3.9+** and the
standard library. The YAML profile helper runs inside the image.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
make test
make lint RUFF=.venv/bin/ruff
```

Live checks require outbound HTTPS:

```bash
HERMES_LIVE_URL=https://council.zavarov.com make test
```

GitHub Actions runs tests, Ruff, Compose validation with example credentials
and Dockerfile checks. It does not receive server/account secrets or deploy
automatically. Never print production `docker compose config`; use `--quiet`.

## References

- [Hermes Docker](https://hermes-agent.nousresearch.com/docs/user-guide/docker)
- [Telegram Login](https://core.telegram.org/bots/telegram-login)
- [Hermes model setup](https://hermes-agent.nousresearch.com/docs/getting-started/quickstart)
- [Sign in with ChatGPT](https://learn.chatgpt.com/docs/sign-in-with-chatgpt)
