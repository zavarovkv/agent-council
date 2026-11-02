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
The read-only health check requires a protected private API, the closed Telegram
provider, a supervised gateway and fresh runtime state. It never starts or
repairs processes as a side effect.

The official image needs a root s6 bootstrap and writable container filesystem
to configure UID/GID and tools. This is a reviewed upstream exception: do not add
`user: hermes` or `read_only: true` without a compatible bootstrap. The actual
gateway runs as the dedicated Hermes user. Maintenance verifier/restic containers
have read-only roots.

| Item | Server location |
| --- | --- |
| Deployment checkout | `/opt/agent-council` |
| Compose settings | `/etc/agent-council/compose.env` |
| Active Compose source / revision | `/etc/agent-council/active-compose.yaml`, `revision` |
| Telegram Client ID / Secret | `/etc/hermes/telegram.env`, root-only |
| Sessions, memory, skills, model login | `/var/lib/agent-council/hermes` → `/opt/data` |
| Agent workspace | `/var/lib/agent-council/workspace` → `/workspace` |
| Nginx virtual host | `/etc/nginx/sites-available/council` |
| Separate HTTPS certificates | `/etc/hermes/letsencrypt` |
| Native profile backup | `/opt/hermes-deploy/docker-migration/hermes-native-before-docker.tar` |
| Retained native profile | `/var/lib/hermes/.hermes` |
| Restore points | `/var/backups/agent-council/` |
| R2 keys / recovery password | `/etc/agent-council/backup.env`, `backup-password`, root-only |

GitHub stores source, templates and commit history. Credentials and live data
stay outside the checkout. Container recreation preserves the bind-mounted
state. This persistence is separate from an off-server disaster-recovery backup.

The existing TravelLine Dashboard at `https://2.29.13.129/` is independent.
Hermes is available at **https://council.zavarov.com/** through Cloudflare,
with a separate origin certificate. The initial `sslip.io` hostname redirects
to the custom domain.

After reviewing the hostname-specific Council route, apply its template with
`sudo python3 scripts/manage.py configure-route`. It validates Nginx, checks the
protected service and reloads gracefully; failures restore only Council's route.
Origin transport failures return a non-cached 503 with `Retry-After: 30`.

## Initial migration

For a **new** Linux host, install Docker and Python, clone this repository to
`/opt/agent-council`, create a dedicated system account named `hermes` and copy
`config/docker.env.example` to `/etc/agent-council/compose.env` with mode 0600.
Set the public HTTPS origin, real host UID/GID (`id hermes`) and dedicated absolute
data/workspace paths outside the checkout. Run as root:

```bash
cd /opt/agent-council
python3 scripts/manage.py install
```

`HERMES_ACCOUNT` defaults to `hermes`; another dedicated host account may be set
explicitly with its matching UID/GID. The image's internal username stays `hermes`.

This initializes a locked profile with **no default owner**. Configure Telegram
and the model interactively. DNS, HTTPS and Nginx are separate host prerequisites:
add only this application's virtual host, run `nginx -t`, then reload gracefully.
The templates document the current server and need review for another hostname.
Compose's dedicated `172.30.90.0/24` subnet must not overlap host/VPN networks.

These scripts target the current Ubuntu server and are one-time operational
tools, not a generic installer or an update command. Historical migration scripts
require the explicit `COUNCIL_RUN_LEGACY_MIGRATION=true` opt-in:

1. `scripts/install-docker.sh` installs Docker from its signed official apt
   repository without upgrading unrelated packages.
2. Build a reviewed historical release in its original migration context.
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
providers are disabled. Until configured, the dashboard remains locked. Verified
explicit-ID logins create the same pin as username enrollment. If an existing pin
disagrees with a newly configured ID, login stays closed: reconcile ownership
deliberately with a private backup rather than deleting the pin to reopen enrollment.

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

## Shared file and knowledge workspaces

After the owner's existing Notion and Drive connections have been verified, run
`python3 scripts/manage.py configure-workspace` on the Docker host. It tests a
copied profile, grants all four native profiles and four Linear executors the
`council_workspace` tool, then verifies the private listener after cutover.
No new OAuth consent or copied provider tokens are needed. The secretary receives
no grant. The gateway owns the broker on port 9123 on the private Compose networks;
the port is not published to the host and is not proxied by the public website.

The tool reads and edits formatted Notion pages beneath CouncilBase and reads,
lists, uploads and updates files in the selected Drive folder. Text delivery goes
to Notion; Linear holds a short summary and verified link. Ordinary uploads are
bounded to 64 KiB; downloads are paginated. Google-native files have text exports,
not native Docs/Sheets/Slides editing through this tool. Existing native Google
Workspace tooling remains available to the coordinator for larger processing.
Updates require a freshly read revision, and writes use durable idempotency keys.
An uncertain provider write must be inspected before creating a new operation.
These revision checks reduce stale edits but are not provider-atomic transactions.

Private `workspace-client.json` files contain distinct revocable internal grants;
the main profile's `.council/workspace/access.json` controls them. Removing a client
rejects its next request without restarting. Shared cloud content is untrusted
task data. Provider credentials and write receipts remain in the profile backup.

Workspace locations are private profile settings in
`/opt/data/council-workspace.json`: `files` identifies the Google Drive folder;
`knowledge_base` identifies the Notion root page. The coordinator's local
`council-workspace` skill describes the filing policy. These settings select
destinations; they do not grant cloud API access. Keep credentials, downloaded
documents and generated skills outside Git and the image.

Google Drive uses Hermes' bundled `google-workspace` integration. Its setup
requires a Google Cloud Desktop OAuth client and user consent. Request only
the needed file services. The pinned upstream setup command requests mail,
calendar and contacts too; Council's setup defaults to the Drive scope. Explicit
service expansion is described in [Google services](google-services.md).
Client credentials and renewable grants
belong in the persistent profile, not in chat, command arguments or logs.
Verify access to the chosen folder before claiming cloud storage is ready.

Create a **Desktop app** client in a dedicated Google Cloud project, enable the
Google Drive API and download its JSON. From the local checkout, run:

```bash
python3 scripts/setup-drive.py --client-json /path/to/downloaded-client.json
python3 scripts/setup-drive.py --verify
```

The JSON travels on SSH stdin. Google's SDK handles PKCE, state and the code
exchange through a temporary callback on the private container bridge. An SSH
tunnel binds only local `127.0.0.1:8765`; no Docker port or firewall rule is added.
The browser must run on the same computer as this command. Open the printed
Google consent URL and approve access. The callback closes after completion or
ten minutes. Grants persist privately as `google_token.json`, in the format
consumed by Hermes' Google Workspace tools. Verification refreshes the grant,
reads the selected folder and checks that it permits new files, without writes.
Both setup and verification hold the shared maintenance lock, since refreshing
can update the saved grant. The callback uses the `agent-council_hermes` bridge;
the secretary's additional network does not change its destination.

The Drive scope grants access across the consenting account's Drive, even though
CouncilBase is the chosen filing destination. Google shows this scope for the
owner's consent. Configure the app's audience and publishing status deliberately;
Google's **Testing** status can limit refresh grants to seven days.

Notion can use its [official remote MCP](https://developers.notion.com/guides/mcp/get-started-with-mcp)
at `https://mcp.notion.com/mcp`, with `auth: oauth`. Prepare it disabled until the
owner authorizes access. In the dashboard's **MCP** page, choose **Authenticate**
for `notion`, complete Notion's consent screen, then enable and test the server.
Hermes handles the HTTPS callback at `/api/mcp/oauth/callback/notion`; no token
needs to be copied into chat. Retain only the needed tools: connection identity,
tool-access metadata, search, fetch, create pages and update a page.

For repositories, pull requests and CI, follow [GitHub MCP setup](github.md).
Its interactive setup uses a dedicated fine-grained token and verifies access
before enabling the native coordinator's connection.

Telegram's long-running status is a gateway display setting, not an agent skill.
Merge [the Telegram display settings](../config/telegram-display.json) into
`display.platforms.telegram` in the native owner's private `config.yaml`,
preserving other keys. `long_running_notifications: generic` keeps the periodic
notification and edits the existing bubble. The replacement phrase catalog
removes English defaults and raw tool names; it deliberately does not claim a
specific action that the gateway has not verified. `tool_progress: off` prevents
separate raw tool-call messages; it does not disable the long-running heartbeat.
The existing interval and cleanup preference remain unchanged. Apply between
turns; a running turn retains the display settings it started with.

OAuth may expose the selected Notion workspace rather than only the preferred
CouncilBase page. The owner must accept that scope. A filing instruction is not
an API permission boundary. If access must be limited to one page, use a separate
Notion internal connection shared only with that page and its descendants,
instead of granting workspace-wide OAuth access.

After authorization, verify a real read of the configured root page/folder and
read back created artifacts. Each provider needs its own authorization. Access
through a connected plugin in a development conversation does not authorize the
server's Hermes runtime. Dexter returns local research artifacts to Qui-Gon;
the coordinator reviews results and files them using its own cloud connection.

Reviewed research reports belong in a dedicated child page of the configured
Notion root. Set `knowledge_base.research` in the private workspace JSON to an
object with `page_id`, `name` and `url` for that section. Each study gets its own
dated child page with the brief, findings, sources, verification dates,
limitations and task/run reference when available. Qui-Gon checks the saved
page's parent and content and maintains a table on the research parent page
with one row per report: date, subject, author, reviewer, task/run reference
when available and page link. Preserve existing rows and read the index back;
if only the index update fails, retry that step without creating another report.
Task status stays on the task board; Drive stores supporting files. Missing
access or a failed write must leave the local report intact and must not create
duplicate sections or reports on retries.

## Декстер Джеттстер: internal competitor researcher

The image includes the official `advanced-delegation` plugin at reviewed commit
`e404b6816b20237a82f6fbe2749b5a576cf75dba`, verified by archive checksum.
It is opt-in; building the image does not create agents or change routing.
Versioned role instructions live in `instructions/dexter/`; the coordinator's
skill is in `instructions/assistant/skills/competitor-research/`. See the
[instruction index](../instructions/README.md) for editing and update behavior.
Mutable profiles, reports,
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

## Public team page and login

The landing includes the canonical team, image-owned portraits and selected models.
The Linear adapter refreshes a private aggregate file every five minutes using its
existing app grant; counts include archived completed issues in configured teams.
A delegated issue is credited once to its delegate, otherwise to its assignee.
No issue titles, comments, user IDs or credentials enter the public response.

`/auth/login/council/team` serves a five-minute snapshot assembled from these counts and
read-only profile configuration/session databases. Tokens are saved-session input
plus output, with session-ID deduplication and attribution by recorded profile.
Cached/reasoning subsets are not added twice. Pruned history and tool-free secretary
replies are outside this metric; it is not provider billing. Missing, malformed or
stale sources produce an unknown value, never a synthetic zero. The page shows no
dates. Other dashboard APIs retain their existing authorization gate.

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
sudo python3 scripts/manage.py doctor
sudo python3 scripts/manage.py deploy
```

`deploy` requires a clean checkout, builds/reuses a Git-SHA-tagged image before
stopping anything, checks idle Hermes, takes a consistent local checkpoint and
tests both old and candidate images against copied state without network access.
A failed upgrade restores the old data, image, authentication and Compose source
together. Displaced directories are retained. Existing SHA tags are reused,
not overwritten; OCI labels record revision and license.

`doctor` inspects actual mounts, capabilities, resource limits, runtime user and
private settings. When the secretary is enabled it also reports worker health,
policy validity and queue counts by state. It omits identities, messages and
credentials. An optional route fault is reported for repair; a healthy process
alone does not prove a model grant is still authorized.

Before the first managed update of an older deployment, record its exact
currently deployed full Git revision:

```bash
sudo python3 scripts/manage.py adopt-release --revision FULL_DEPLOYED_COMMIT_SHA
```

It validates the existing deployment record/image label and saves that release's
Compose source without restarting. It refuses to replace an existing active
source. Do not infer the deployed revision from an updated checkout.

See [backup and recovery](backups.md) for rollback and encrypted R2 setup. The
old native profile remains a manual migration recovery point; it does not contain
state accumulated after Docker cutover.

Optionally install root-owned `/etc/agent-council/protected-services.json` from
`config/protected-services.example.json`. The controller compares unrelated
services' process IDs, explicit configuration hashes and verified HTTPS responses
before/after maintenance; it never controls them. This deployment protects
TravelLine. Access/credential setup and managed maintenance share a lock; finish
interactive model/OAuth setup before updating.

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

## Public landing and indexing

The hostname-specific Council route serves the existing public roster/login view
at `/` with HTTP 200; signing in continues to `/profiles`. Private dashboard and
API routes retain the upstream session gate. Apply the versioned Nginx template
with `python3 scripts/manage.py configure-route`; it validates syntax, retains
rollback and gracefully reloads only the shared proxy configuration.

The rendered HTML contains a canonical URL, description, Open Graph/Twitter card,
WebSite JSON-LD and static portrait/contact links. `/robots.txt` advertises a
one-page sitemap. `X-Robots-Tag` excludes private interfaces from indexing; it is
not an authorization mechanism. The RU/EN switch is a reading preference on one
URL, so no invented language URLs or hreflang alternates are published. Indexing
and search appearance remain decisions of the search engines.
