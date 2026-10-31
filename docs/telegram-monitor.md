# Telegram source for agent research

Hermes already provides the agent, native cron, research tasks, Notion tools and
Telegram delivery. Configure a digest through the personal assistant and reviewed
skills. No separate digest scheduler, delivery service or report database is needed.

The optional `telegram-reader` supplies data from a closed source where the owner
cannot add a bot. It uses Telegram's Client API with an existing member account
and reads one pinned group/channel. It never joins, sends messages, acknowledges
reads or downloads attachments. Membership is required; this is not an access bypass.

The session is **not** a Telegram-issued read-only permission: its key technically
authorizes the account. Only the collector receives the root-only host env file.
No agent, public responder or Linear executor receives that key. Revoke this
client in Telegram **Settings → Devices**.

## Connect the source

Deploy an image with `telegram-reader-v1`. Obtain a user API ID and hash at
[my.telegram.org](https://my.telegram.org), under API development tools, following
[Telegram's API setup](https://core.telegram.org/api/obtaining_api_id).
From the local checkout run:

```bash
bash scripts/setup-telegram-reader.sh SOURCE_USERNAME
```

Enter API ID/hash, phone, login code and optional two-step password only in its
private terminal prompts. No credentials belong in chat, argv, Git or logs.
Authorization verifies real membership/access and pins immutable account/source
IDs. Reassigned usernames fail closed, including during reauthorization.

Setup verifies a copied profile, runs a real bounded collection and enables the
07:15 Europe/Moscow collector timer. The agent receives non-secret source metadata
in `${HERMES_HOME}/.council/telegram-reader.json` and daily evidence under
`/workspace/monitoring/telegram/raw/YYYY-MM-DD.json`.
Existing valid login is reused; `--reauthorize` explicitly replaces it while
preserving the source pin. Interrupted later setup can retry without another login.
The standard unit templates assume `/opt/agent-council` and `/etc/agent-council`;
review them before using different deployment paths.

## Let the assistant configure the digest

Ask the personal assistant to configure native Hermes cron: research at 07:20
Moscow, review/file the result under the existing private Notion research section,
and send the concise report/link at 08:00. Reviewed skills describe the workflow.
The coordinator uses the native Dexter board and `research_archive`, which verifies
the saved page and parent index. A useful public-source report can proceed while
Telegram is unavailable, with that gap stated explicitly.

The delivery job can use native `context_from` to receive the research job's latest
result, then check the current day, task and actual filing status before returning
its answer. Native cron owns delivery and retries. A model-written link alone is
not proof of publication. No separate sender or product-specific JSON handoff is
needed. The pinned release has no per-job timezone; keep its native scheduler in
UTC and use `20 4 * * *` / `0 5 * * *` for Moscow. Do not silently shift other jobs.

## Operations and data

```bash
sudo python3 scripts/manage.py collect-telegram-monitor
sudo systemctl status agent-council-telegram-reader.timer
```

The receipt contains only status, period, coverage and candidate count. Collection
processes up to 5000 messages and retains at most 300 context messages / 2 MiB per
day, keeping seven days of evidence. Small windows are retained whole; larger
windows prioritize OTA keyword context and recent text and are marked partial.
Text/captions are covered; images, audio and attachments are outside the scan.
No sender names or IDs are collected as metadata. Missing data is not no events.

The collector has its own network, no ports, read-only root, bounded resources,
no capabilities and only the evidence-directory mount. Docker logging is disabled.
The private session is `/etc/agent-council/telegram-reader.env`, outside Git and
agent mounts. Encrypted backups include it and source evidence; dependencies and
Docker layers stay in the rebuildable image. Snapshots also freeze a collector
surviving a crashed command. Recovery restores its grant and collector timer;
normal Hermes data recovery already covers the agent's native cron jobs.
