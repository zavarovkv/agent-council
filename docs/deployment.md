# Deployment guide

This guide covers the current Ubuntu deployment and its operational scripts.
See the [project overview](../README.md) for scope and development links.

## Deployment

Prerequisites: Linux, Docker Engine 29+, Docker Compose **2.30+**, Bash, OpenSSH
and Python **3.11+** for setup scripts. The current server has Engine 29.9.2 and Compose
5.6.1. The official Hermes **v0.22.5** base image is pinned by SHA-256.

`Dockerfile` adds the owner policy as immutable application code. `compose.yaml`
limits Hermes to one CPU, 2 GiB RAM, 256 processes, bounded logs, a dedicated
bridge network and a loopback port. No host Docker socket or TravelLine
directories are mounted. The official image starts s6 as root and drops the
actual Hermes process to the configured unprivileged UID/GID.

Compose runs `gateway run` with `HERMES_DASHBOARD=1`. The upstream s6 supervisor
manages both the model/messaging gateway and the dashboard in the same container.
A dashboard-only command does not register gateway services and cannot execute
model requests. No host systemd service or detached shell process is needed.
The health check requires the protected dashboard and a live supervised gateway.

| Item | Server location |
| --- | --- |
| Deployment checkout | `/opt/agent-council` |
| Compose settings | `/etc/agent-council/compose.env` |
| Telegram Client ID / Secret | `/etc/hermes/telegram.env`, root-only |
| Sessions, memory, skills, model login | `/var/lib/agent-council/hermes` → `/opt/data` |
| Agent workspace | `/var/lib/agent-council/workspace` → `/workspace` |
| Nginx virtual host | `/etc/nginx/sites-available/council` |
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
[the native deployment record](native-deployment.md) for its setup details.

## Telegram sign-in

In `@BotFather`, choose or create a bot and open **Login Widget**. Register the
current public origin and its `/auth/callback` URL:

```text
https://council.zavarov.com
https://council.zavarov.com/login
https://council.zavarov.com/auth/callback
```

Register `/login` as well: the official JavaScript SDK uses the current page
URL when opening its popup. The OIDC fallback uses `/auth/callback`.

From the project on your Mac, run:

```bash
bash scripts/setup-telegram.sh
```

Enter the Login Widget **Client ID** and **Client Secret** in the SSH terminal.
The secret is hidden and saved directly on the server. The Bot API token is
a different credential. The script recreates Hermes to load the environment,
checks its auth gate, then removes the setup-status page.

Explicitly configure the owner: a numeric `HERMES_TELEGRAM_ALLOWED_ID` is
preferred, or `HERMES_TELEGRAM_OWNER_USERNAME` can bootstrap the first login.
The current deployment uses `@zavarov`; a new installation has **no default
owner**. Hermes verifies the OIDC signature, issuer, audience and expiry before the owner policy pins the
numeric Telegram ID. Later logins require that ID even after a username change
or reassignment. Corrupted identity state denies access. Alternative dashboard
providers are disabled. Until configured, the dashboard remains locked.

### Add or remove people

From this project on your Mac, replace `123456789` with the person's numeric
Telegram account ID:

```bash
bash scripts/telegram-access.sh list
bash scripts/telegram-access.sh add 123456789
bash scripts/telegram-access.sh remove 123456789
```

One bot serves all invited accounts. The command stores invited IDs in
`HERMES_TELEGRAM_ALLOWED_IDS` in `/etc/hermes/telegram.env` on the server, outside
Git. The owner is configured separately and remains authorized. Each change
recreates only Hermes to apply the list and close existing connections; valid
tokens for removed accounts are then rejected. Credentials and agent state are
preserved. A failed restart restores the previous settings.

For manual administration, the same setting is a comma-separated list, for
example `HERMES_TELEGRAM_ALLOWED_IDS=123456789,987654321`; apply it by recreating
the Hermes service. Do not put real authentication files in Git or share them.
Invited accounts have the same dashboard/tools/history access as the owner;
these are trusted collaborators, not isolated tenants.

No bot application needs to run. Each independent direct-Telegram installation
registers its own Login client. Standard OIDC and optional centralized login are
[optional future architecture](authentication-options.md), not implemented features.
Never distribute a shared Telegram secret inside an image.

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

After sign-in, check the runtime from the server checkout:

```bash
bash scripts/compose-server.sh exec --user hermes hermes hermes gateway ensure
bash scripts/compose-server.sh exec --user hermes hermes hermes chat --oneshot --query 'Reply exactly OK.'
```

The model selection and renewable account credentials belong in the persistent
profile, not in the Dockerfile or Git. A Telegram messaging bot is configured
separately from the dashboard's Telegram login application.

`make test-integration` also boots the actual Compose gateway/dashboard command
with temporary in-memory data, no network, no published ports and no credentials.
It checks gateway readiness and rejection of unauthenticated dashboard API access.

## Dexter Jettster: internal competitor researcher

The image includes the official `advanced-delegation` plugin at reviewed commit
`e404b6816b20237a82f6fbe2749b5a576cf75dba`, verified by archive checksum.
It is opt-in; building the image does not create agents or change routing.
Versioned role instructions live in `agents/dexter/`. Mutable profiles, reports,
credentials and memory remain in the persistent data volume.

After configuring the coordinator's Codex account, stop its gateway, provision
the profile as the runtime user, then start the gateway again:

```bash
bash scripts/compose-server.sh exec hermes /command/s6-svc -d /run/service/gateway-default
bash scripts/compose-server.sh exec hermes /command/s6-svwait -d -t 30000 /run/service/gateway-default
bash scripts/compose-server.sh exec --user hermes hermes python3 /opt/agent-council/provision-researcher.py
bash scripts/compose-server.sh exec hermes /command/s6-svc -u /run/service/gateway-default
bash scripts/compose-server.sh exec --user hermes hermes hermes gateway ensure
```

Wait for the gateway to stop before provisioning; do not interrupt an active
conversation. The installer uses Hermes' native profile API and config writer.
It creates `dexter` without cloning the coordinator's identity, chats, channels
or credentials. It selects the same model and uses Hermes' root credential-pool
fallback for the renewable Codex grant. Reports belong in
`/opt/data/profiles/dexter/research/`.

The coordinator gets the `competitor-research` skill and an explicit
`delegate_task` route with `profile: dexter`. Other delegation tasks retain their
usual route. Dexter has no separate Telegram bot; Qui-Gon collects the brief,
delegates and reviews the sourced result before replying to the owner. A new
task starts with fresh worker context; pass needed facts and prior report paths
in the brief. In an existing Telegram session, use
`/delegation profile_routes --now` to refresh its delegation schema.

Rerunning setup preserves reports and unrelated settings. It refuses unmanaged
profiles, conflicting delegation policies and edited managed instructions.
The original coordinator config is retained privately as
`/opt/data/config.yaml.before-dexter`; restore it and restart the gateway to
disable this route. Back up the whole data directory before deployment.

Profiles share the trusted container and its execution privileges. A role prompt
is not a filesystem sandbox or a boundary for untrusted users. Routed workers
inherit the parent's permitted tools, with Hermes' built-in delegation exclusions.

### Telegram chat automation

The messaging bot currently handles direct messages from explicitly allowed
users. Connecting it under Telegram **Settings → Chat Automation** alone does
not enable a secretary: this Hermes adapter has no business-connection reply
routing. Do not expand the execution allowlist to all contacts as a workaround.
A secretary needs separate conversation state and restricted execution, plus
explicit rules for drafts versus automatic replies before activation.

## Login appearance

The landing page uses [Beauty.css](https://github.com/zavarovkv/beauty-css) as an
exact npm dependency, currently **0.4.0**. `package-lock.json` records registry
integrity. A digest-pinned Node 24 build stage runs `npm ci --ignore-scripts`;
only the CSS, package metadata and MIT license enter the final Hermes image.
The build stage adds no Node runtime to the final image; Hermes retains the
tools supplied by its upstream image. Beauty source is not copied into this
repository. Pages use the package unchanged, with no local CSS or style overrides.
Shared styling improvements belong in Beauty.css and arrive through a package update.

Dependabot proposes npm updates weekly. An update requires passing checks,
review, image rebuild and deployment; changing the Beauty repository does not
silently change production. To update manually, run `npm install --save-exact
beauty-css@VERSION --ignore-scripts` and commit both package files.

The header uses the **official Telegram Login JavaScript SDK and its default
blue web button**, loaded from `https://oauth.telegram.org/js/telegram-login.js?6`.
The SDK supplies the button styles and icon; Council does not override them.
Beauty's documented `not-beauty` class excludes this button from its styling.
The header groups both sign-in methods in Beauty's `beauty-nav-end` helper to align
them to the right, including when navigation wraps. No project CSS is needed.
It opens Telegram's popup. The local initializer in `assets/login/telegram-widget.js`
posts the resulting ID token to the server; it does not trust decoded browser
claims or persist tokens in browser storage. A server-side OIDC/PKCE link stays
available if the SDK cannot load. The page has no footer and supports system
light/dark colors, mobile layout and keyboard focus.

The widget bridge uses same-origin HTTPS POSTs, a short-lived single-use nonce,
a hardened browser cookie, bounded rate/capacity limits, real JWT verification
and the same owner/invitation policy as other sessions. It creates standard
Hermes HttpOnly/Secure session cookies. After either login flow verifies Telegram's
token, the app issues a random, revocable session valid for **30 days**, independent
of that token's short lifetime. Only a SHA-256 hash and verified identity are stored
in `/opt/data/dashboard-sessions.sqlite3`, with private file permissions. This survives
container restarts. Logout revokes the record; every request rechecks owner/invitation
access. Set `HERMES_TELEGRAM_SESSION_DAYS` to 1–90 days to change the lifetime.
Existing token-based cookies require one new login to gain the persistent session.
Database failures return 503 and retain the cookie instead of logging the user out.
Challenges are process-local;
the current container runs one backend. Multiple workers need a shared challenge store.

Landing HTML and initializer are in `assets/login/` and ship in the Docker image
alongside the exact Beauty.css package artifact.
`scripts/install-login-theme.py` verifies the pinned Hermes renderer and auth
router before installing templates and the two widget bootstrap routes. Token
verification and session cookie primitives come from Hermes. Updating Hermes
requires a compatibility review.

Container startup initializes a fresh profile's `state.db` using Hermes' official
`SessionDB` under its runtime user and profile-maintenance lock. Existing stores
are retained. Browsing history therefore works before a gateway or chat bot is started.

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

When upgrading from the initial quoted authentication env file, first run
`python3 scripts/migrate-auth-environment.py` as root on the server, **before**
recreating a container with this version of Compose. It preserves a root-only
backup and writes literal raw values. The `.env.format` marker makes repeat
runs safe. Keep a copy of the previous checkout/image and restore the previous
env file together if rolling back across this format change. In raw env files,
quotes are part of the value; do not add shell-style quoting.

The `hermes-cert-renew.timer` uses the pinned maintenance-only Certbot container.
It mounts only this deployment's ACME directories. It has no access to the
existing application's virtual environment. The renewal script validates and
gracefully reloads Nginx only when a certificate changes. On the server, check
renewal with `bash scripts/renew-certificates.sh --dry-run`.

## Custom domain

Cloudflare has `A council → 2.29.13.129` with proxying enabled. The HTTP-01
challenge works through the proxy, and the origin has a valid Let's Encrypt
certificate. Keep Cloudflare's connection to the origin encrypted; use Full
(strict) mode for this hostname. The one-time switch is recorded in
`scripts/activate-council.sh`, which has a configuration rollback. Both Nginx
virtual hosts keep their challenge routes for renewal. The existing TravelLine
virtual host remains unchanged.

## References

- [Hermes Docker](https://hermes-agent.nousresearch.com/docs/user-guide/docker)
- [Telegram Login](https://core.telegram.org/bots/telegram-login)
- [Hermes model setup](https://hermes-agent.nousresearch.com/docs/getting-started/quickstart)
- [Sign in with ChatGPT](https://learn.chatgpt.com/docs/sign-in-with-chatgpt)
