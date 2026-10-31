# Telegram competitor monitoring

For a source where the owner cannot add a bot, the optional `telegram-reader`
container uses Telegram's Client API with an existing member account. It reads
one pinned group/channel. It never joins, sends messages, acknowledges reads or
downloads attachments. Telegram membership is required; this is not an access bypass.

The account session is **not** a Telegram-issued read-only permission: its key
technically authorizes the account. Only the collector receives it, from a
root-only host env file. No agent, public responder or Linear executor receives
that file or session. Revoke this client in Telegram **Settings → Devices**.

## Connect

Deploy a release with `telegram-monitor-v1` and install a reviewed `travel-digest`
skill in the trusted personal coordinator. The daily workflow uses the existing
native Dexter research board and `research_archive` integration; configure their
private Notion destination and coordinator model first. The separate Linear
executors do not receive private Telegram or Notion access.

Obtain a user API ID and hash at [my.telegram.org](https://my.telegram.org), under
API development tools, as described in [Telegram's API setup](https://core.telegram.org/api/obtaining_api_id).
From the local checkout, run:

```bash
bash scripts/setup-telegram-reader.sh SOURCE_USERNAME
```

Enter API ID/hash, phone, login code and optional two-step password only in its
private terminal prompts. No token, session string or code belongs in chat,
command arguments, Git or logs. A new bot is unnecessary; the existing assistant
bot delivers reports. Authorization first verifies real source access and pins
the immutable channel ID and account ID. Reassigned usernames fail closed.

Setup preserves an existing valid login. Use `--reauthorize` only to replace it;
the source pin and report owner must remain the same. Interrupted later setup
can be retried without another login. Existing managed cron/script edits are
preserved and require reconciliation; other native jobs remain unchanged.

## Daily workflow

| Moscow time | Action |
| --- | --- |
| 07:15 | Host systemd timer collects the preceding 24 hours of source text. |
| 07:20 | Native coordinator cron delegates real research to Dexter and reviews it. |
| 08:00 | Native no-agent cron sends a verified Notion report or an explicit failure status. |

The pinned Hermes release has no per-job timezone. Its native scheduler must stay
in UTC; setup verifies this before installing `20 4 * * *` and `0 5 * * *` jobs.
The collector timer uses `Europe/Moscow` explicitly. Both native jobs are created
paused, verified on a copied profile, and activated only after source collection
and collector timer installation. Native cron retains its own durable delivery.

The research skill must use current primary public sources, separate company
claims from independent evidence, preserve uncertainty and avoid a fixed news
quota. Create one idempotent native research task per reporting day, review it,
and call `research_archive` into `knowledge_base.research.page_id`. Its existing
verified parent table remains the research index.

The reviewed coordinator writes `monitoring/telegram/reports/YYYY-MM-DD.json`
inside the workspace with `schema: 1`, `task_id`, aware UTC `window_start` and
`window_end`, and a concise `summary` of at most 3000 characters. The delivery
script checks the current window, completed filing ledger, real task, report
date and configured Notion parent. It derives the page link from that ledger;
a model-written URL cannot prove publication. Missing evidence is not “no events”.

## Operations and storage

```bash
sudo python3 scripts/manage.py collect-telegram-monitor
sudo systemctl status agent-council-telegram-reader.timer
```

Collection prints only a receipt: source status, period, coverage and candidate
count. It scans at most 5000 messages and retains at most 300 context messages
and 2 MiB per daily artifact. It keeps seven days of source artifacts. Selection
retains small windows whole; larger windows prioritize OTA keyword context and
recent text, rather than providing exhaustive semantic analysis.
Reports must state these limits. Coverage concerns text and captions; images,
audio and attachments are outside the scan. Truncation and source failures are
explicit. No participant names or sender IDs are collected as metadata.

The collector has its own network, no ports, read-only root, bounded resources,
no added capabilities and only the evidence-directory mount. Docker logging is
disabled for it. Host state is under the existing workspace and
`/etc/agent-council/telegram-reader.env`, outside Git. Encrypted backups include
the session file and evidence; SDK dependencies and Docker layers stay in the
rebuildable image. Snapshots also freeze a surviving collector after a crashed
operator command. Recovery restores the grant, native jobs and collector timer.
