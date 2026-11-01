# Early-access requests

The public landing opens Qui-Gon's Telegram chat with a localized request.
`scripts/sync-waitlist.py` captures explicit early-access requests from the
existing verified public-bot and business-message queues, then saves them to
one configured Notion data source. It runs without model calls or new credentials.

The worker reads only `plugin-data/council-secretary/public-queue.sqlite3` and
`queue.sqlite3`, in SQLite read-only mode. It does not read owner history,
secretary model history, unrelated Telegram chats or guest conversations.
Ordinary messages, cancelled messages, and messages before the configured start
are excluded. Both landing messages and `/waitlist` are recognized; explicit
RU/EN opt-ins mentioning early access or the waitlist are also accepted.
This is intent matching for registration, not a general conversation classifier.

One Telegram numeric ID produces one row. A changed username or repeat message
does not create a second applicant. The first request and its original contact
snapshot are retained; the numeric ID remains the identity if a username changes.
Operator edits to the status are never overwritten. No dashboard access is granted.
Long requests keep a bounded preview in the table and their complete literal text
inside the row's page; readback verifies both.

Requests are captured in a private durable SQLite store before cloud operations.
Notion outages before creation leave them queued for retry. Successful writes
require readback of the expected data source, Telegram ID and request text.
If the response to creation is lost, the next run reconciles the unfiltered
Notion view, including archived rows. An unresolved ambiguous creation remains
pending and fails the service visibly; it is never blindly repeated. Inspect the
target and retained request before manually returning such a row to `queued`.
Duplicate remote IDs, changed schema, filtered views and incomplete pagination
stop synchronization rather than creating plausible duplicates.

## Configure

Create a private Notion database under the coordinator's existing connected
workspace. Its properties must be:

| Property | Type |
| --- | --- |
| Заявитель | Title |
| Telegram | URL |
| Telegram ID | Text |
| Дата заявки | Date |
| Статус | Select: Новая, Приглашен, Отложена |
| Заявка | Text |

Keep one unfiltered table view for reconciliation. Create separate filtered views
for human triage. The data source and view must be visible to the **server's**
existing Notion connection, not only to the developer's connection.

Outside Git and the image, create
`HERMES_DATA_DIR/.council/waitlist/config.json` owned by the configured Hermes
UID/GID, directory mode 0700 and file mode 0600:

```json
{
  "schema": 1,
  "data_source": "collection://DATA_SOURCE_UUID",
  "view": "view://UNFILTERED_VIEW_UUID",
  "since": 1793491200
}
```

`since` is the explicitly chosen UTC Unix start time. The source queues retain
14 days, so a worker disabled longer cannot recover older uncaptured messages.
Captured applicants are retained until the operator removes them under the
project's retention policy; do not commit the database or copy it into logs.

From the reviewed server checkout:

```bash
bash scripts/sync-waitlist.sh --check
bash scripts/sync-waitlist.sh
install -m 0644 config/agent-council-waitlist.service /etc/systemd/system/
install -m 0644 config/agent-council-waitlist.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now agent-council-waitlist.timer
```

The launcher streams the reviewed source into the existing Hermes container as
the unprivileged `hermes` user. It does not patch the running image or restart
agents. The existing main-profile Notion connection owns cloud writes; the
tool-free secretary receives no Notion credentials or workspace permissions.
No reply is sent by this worker, and no on-page success is fabricated.

The timer runs one minute after each completion. Idle runs make no Notion calls.
Output contains only counts and safe error identifiers. Check
`systemctl status agent-council-waitlist.service` and its journal for pending or
failed writes. Roll back with `systemctl disable --now agent-council-waitlist.timer`;
retain configuration and receipts to preserve deduplication on reactivation.

Validation: `python3 -m unittest discover -s tests -p test_waitlist.py -v`.
Production queue contents must not be used as test fixtures. An end-to-end cloud
check uses explicitly marked synthetic records and no Telegram sends.
