# Previous native Hermes deployment

Historical record of the initial systemd deployment. See the root README for
Docker Compose operations; the native profile remains available for rollback.

An isolated Hermes Agent deployment alongside the existing TravelLine Dashboard.
The existing application stays at `https://2.29.13.129/`. Hermes uses
`https://hermes.2.29.13.129.sslip.io/` and requires the owner's Telegram account.

Hermes is pinned to release **v0.22.5**, commit
`bf0203a794a07aac3ff764972ab9087baa4e4f4e`. The official installer prepares its
own Python 3.14.9 and Node environments. It does not install host packages or
reuse the existing application's virtual environment.

## Layout

- `src/telegram_auth/`: Telegram OIDC provider and persistent owner authorization.
- `config/`: separate Nginx and systemd units, example secret configuration.
- `assets/`: a local setup-status page shown until Telegram login is configured.
- `scripts/`: SSH access, deployment and interactive account setup.
- `tests/`: owner authorization and opt-in live HTTPS checks.

## Server layout and isolation

| Component | Location |
| --- | --- |
| Hermes installation | `/opt/hermes/app` |
| Dedicated unprivileged account | `hermes` |
| Hermes profile and model credentials | `/var/lib/hermes/.hermes` |
| Agent workspace | `/var/lib/hermes/workspace` |
| Telegram credentials | `/etc/hermes/telegram.env`, root-only mode 0600 |
| Hermes service | `hermes-dashboard.service`, localhost port 9119 |
| Additional Nginx virtual host | `/etc/nginx/sites-available/hermes` |
| Separate certificate storage | `/etc/hermes/letsencrypt` |
| Certificate renewal | `hermes-cert-renew.timer`, twice daily |
| Original Nginx backup and checksums | `/opt/hermes-deploy` |

The service is limited to one CPU and 2 GiB RAM (1.5 GiB soft limit), has no
privileged capabilities or sudo, and cannot access `/opt/tl-dashboard`,
`/etc/nginx`, `/etc/letsencrypt`, or users' home directories. The root-owned
Telegram plugin is read-only inside the service. Only Nginx exposes Hermes;
port 9119 binds to loopback.

The `sslip.io` hostname resolves the embedded IP without a separate DNS account.
The existing Nginx virtual host, its certificate and application process are
preserved. New routing uses `nginx -t` and a graceful reload. The publication
script removes only its new virtual-host symlink if a deployment step fails.

## Custom domain: council.zavarov.com

The custom hostname is being prepared as the replacement for the initial
`sslip.io` address. Its DNS zone is served by Cloudflare. Create an `A` record
named `council` pointing to `2.29.13.129`, with TTL `Auto` and proxy status
`DNS only` for the initial certificate issuance.

`scripts/stage-council.sh` runs on the server as root. It prepares separate
HTTP/HTTPS configuration candidates and backs up the current Hermes settings
under `/opt/hermes-deploy/council`. Only the new HTTP challenge virtual host is
enabled at this stage. The existing Hermes public URL and Telegram setup remain
unchanged until DNS points to the server and its certificate can be issued.

After certificate issuance, activate the candidate HTTPS virtual host, change
`dashboard.public_url` to `https://council.zavarov.com`, update the Telegram
setup script to that origin, and restart only `hermes-dashboard.service`.
Validate the owner-only auth gate and existing application's checksums and
response. The BotFather Allowed URLs must then use:

```text
https://council.zavarov.com
https://council.zavarov.com/auth/callback
```

The original `sslip.io` virtual host can subsequently redirect to the custom
domain, preserving its own HTTP challenge route for certificate renewal.

## Finish Telegram login

1. In Telegram's `@BotFather`, create or choose a dedicated bot, then open
   **Login Widget**. Add both Allowed URLs:
   - `https://hermes.2.29.13.129.sslip.io`
   - `https://hermes.2.29.13.129.sslip.io/auth/callback`
2. From this project on your Mac, run:

   ```bash
   bash scripts/setup-telegram.sh
   ```

3. Enter the **Client ID** and **Client Secret** shown in Login Widget. The
   secret is hidden and is saved directly on the server. The Bot API token is
   a different credential and is not needed for website login.
4. Open the Hermes HTTPS address and sign in as **@zavarov**.

Until client credentials are configured, the auth gate stays enabled and no
account can sign in. Telegram's signature, issuer, audience and expiry are
verified by Hermes' OIDC provider before owner authorization. The first verified
`@zavarov` login atomically pins its numeric Telegram ID. All subsequent logins
must have that same ID, even if the owner changes username or someone else
later obtains `@zavarov`. A known numeric ID can instead be set in
`HERMES_TELEGRAM_ALLOWED_ID` before first login. A malformed pin denies access.

Do not enable the bundled `basic`, `nous`, or `self-hosted` dashboard providers:
they are disabled to prevent alternative login routes bypassing the owner policy.
Model providers are configured separately.

## Connect your ChatGPT / Codex subscription

Run from the project on your Mac:

```bash
bash scripts/setup-model.sh
```

Select **ChatGPT or Codex Subscription** in `hermes model`. Open the displayed
OpenAI URL in your own browser, sign in with the intended account, confirm the
device code, then select an available model. The command runs as `hermes` so
credentials land in the correct profile, not root's home. If OpenAI refuses
device-code sign-in, enable it in your ChatGPT security/workspace settings or
use the browser flow described in Hermes' documentation.

No ChatGPT password or auth file needs to be sent in chat. Model login is
independent of Telegram login to the web dashboard. A real model response can
only be checked after this login is completed.

## Operations

Prerequisites for local scripts: macOS/Linux, Bash, OpenSSH, and the dedicated
SSH identity at `~/.ssh/agent_council_ed25519`. Override with `COUNCIL_SSH_KEY`
and `COUNCIL_SSH_HOST` if needed. The remote bootstrap targets Ubuntu with
systemd, Nginx, git, curl and a healthy existing TravelLine service.

```bash
bash scripts/inspect-server.sh
bash scripts/server.sh 'systemctl status hermes-dashboard.service --no-pager'
bash scripts/server.sh 'journalctl -u hermes-dashboard.service -n 50 --no-pager'
```

For initial deployment, `bootstrap-hermes.sh` runs on the server first. Upload
`src/`, `config/`, `assets/`, `scripts/` and `tests/` to `/opt/hermes-deploy/assets`, then run
`prepare-hermes.sh` and `publish-hermes.sh` there as root. Bootstrap refuses to
overwrite an existing installation. These are operational scripts, not a
command to rerun blindly. Certificate renewal invokes the existing standalone
Certbot binary with Hermes-specific config/work/log directories; the existing
certificate-renewal unit and certificate files are not changed.

To take only Hermes offline, run:

```bash
bash scripts/server.sh 'systemctl stop hermes-dashboard.service'
```

To remove only its public route, unlink `/etc/nginx/sites-enabled/hermes`, run
`nginx -t`, then gracefully reload Nginx. Keep data and backups for recovery.
Never restore the entire Nginx backup over unrelated subsequent configuration
changes; normally removing the additional virtual host is sufficient.

## Development and checks

Authorization unit tests require Python **3.9+** and only the standard library.
The Hermes plugin runs under Hermes' managed Python **3.14**. Install the pinned
formatter/linter into a local virtual environment:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
make test
make lint RUFF=.venv/bin/ruff
```

Live checks are optional and require outbound HTTPS:

```bash
HERMES_LIVE_URL=https://hermes.2.29.13.129.sslip.io make test
```

These verify the owner-only auth provider, refusal of unauthenticated sensitive
API requests, forged credentials and an unauthenticated chat WebSocket. They
do not use real Telegram or OpenAI credentials.

## Primary documentation

- [Hermes web dashboard](https://hermes-agent.nousresearch.com/docs/user-guide/features/web-dashboard)
- [Telegram Login and OIDC](https://core.telegram.org/bots/telegram-login)
- [Hermes account setup](https://hermes-agent.nousresearch.com/docs/getting-started/quickstart)
- [OpenAI Sign in with ChatGPT](https://learn.chatgpt.com/docs/sign-in-with-chatgpt)
