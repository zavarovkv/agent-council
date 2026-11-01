# Monitoring

Two independent checks cover different failures. A host timer sends private
operational alerts to the pinned Telegram owner. GitHub Actions checks public
HTTPS from outside the server, without deployment credentials.

## Host notifications

On the Docker host, after verified dashboard owner enrollment and bot setup:

```bash
sudo python3 scripts/manage.py configure-monitoring
sudo install -m 0644 config/agent-council-monitor.{service,timer} /etc/systemd/system/
sudo systemctl daemon-reload
sudo python3 scripts/manage.py monitor --test-notification
sudo systemctl enable --now agent-council-monitor.timer
```

The root-only `monitor.json` pins the current numeric owner ID and records whether
R2/private instruction automation is expected. Notifications read the bot's
existing private credential; no credential copy enters Git, unit files or CI.
Changes to the profile owner pin cannot redirect messages to another account.
The owner must have started this bot's direct chat so Telegram permits delivery.

Every five minutes the monitor checks service readiness/isolation, queue delay,
safe provider outcome categories, backup verification age, automation and free
disk space. A backup older than 36 hours or a failed backup unit needs attention.
Business/public queue thresholds are three/ten minutes. Managed instruction
drift, rejected releases and disabled/failed updates also raise an incident.
When research filing is configured, its timer and pending age are checked too;
a filing waiting more than 30 minutes needs attention.

One notification covers a new/changed incident; recovery is reported once. State
survives restarts. If Telegram does not confirm delivery, the next poll retries;
an ambiguous network response can therefore produce a duplicate alert. It never
retries assistant conversation messages. Active maintenance holds the operations
lock, so monitoring skips that poll without clearing earlier incidents.
Instruction/research timers also defer a busy lock or active conversation to
their next tick without recording a failed unit. Invalid/stale state, rejected
releases and other errors still fail; daily backup failures are never suppressed.

Owner messages use short Russian sentences without diagnostic headers. Instruction
alerts distinguish unavailable files, local edits, rejected revisions, stopped
schedules and failed updates. Recovery confirms that the checks pass again; it
does not claim a new instruction version was installed. Keep the operations lock
held across any planned timer pause and its restoration to avoid a false alarm.

Hermes shutdown/restart notices are registered through the native plugin locale
API in `src/telegram_secretary/__init__.py`. Only these two message keys are
overridden, in Russian for both the default English UI and Russian UI. Other
translations and upstream shutdown delivery are unchanged. The notice asks the
owner to write after reconnection; it does not promise an automatic task resume.

Inspect locally:

```bash
sudo python3 scripts/manage.py monitor
sudo systemctl status agent-council-monitor.timer
sudo journalctl -u agent-council-monitor.service
```

Diagnostics contain fixed categories/counts, not prompts, provider exception
bodies, sender IDs or credentials. Conversation and instruction source remain
private. Provider recovery is confirmed by a later successful generation; the
monitor does not spend tokens on synthetic background requests.

## Independent public check

Set the nonsecret repository variable `COUNCIL_MONITOR_URL` to your HTTPS origin.
For this repository:

```bash
gh variable set COUNCIL_MONITOR_URL --body https://council.zavarov.com
gh workflow run public-health.yaml
```

The workflow runs approximately every 15 minutes. It verifies public bootstrap
fields, closed private APIs/WebSocket, forged credential rejection and the login
route. It uses no real credentials and changes no application data. An edge 403,
TLS failure or wrong auth schema fails the check rather than being counted as
proof of the app gate. Forks remain inactive until their own variable is set.

Enable GitHub Actions failure notifications in the owner's GitHub account. This
external check reports through GitHub; it cannot use the host's Telegram key
when the host is unreachable. Scheduled Actions can be delayed or disabled after
repository inactivity, so this is a lightweight check rather than an uptime SLA.

See [GitHub workflow notifications](https://docs.github.com/en/actions/concepts/workflows-and-actions/notifications-for-workflow-runs)
and [scheduled events](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule).
