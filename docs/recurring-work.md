# Recurring team reports

Hermes Cron is the only scheduler and Telegram sender. The Linear adapter admits
one owner-authorized issue per schedule/date; Qui-Gon delegates through the same
specialist workflow as interactive tasks. Notion holds the formatted deliverable.
The runtime records stages and delivery receipts in the existing adapter database.
It does not create a second task board or a second message-sending service.

## Setup and management

Deploy `recurring-tasks-v1`, authorize the four Linear agents, shared workspace and
owner Telegram account, then run:

```bash
sudo python3 scripts/manage.py configure-recurring
```

This checks a copied profile, snapshots state and installs a separate private
main-profile grant. It does not expose Linear OAuth credentials to the assistant.
Install compatible reviewed `recurring-work`/`travel-digest` skills. Ask Qui-Gon to
configure the report through `recurring_tasks` with a stable ID, self-contained
brief, weekdays (Monday=0), reporting window, preparation and delivery times.
Keep the brief about the requested work and its acceptance criteria, using short
paragraphs and lists. Generated Linear descriptions show the brief, reporting
period, deadline and Notion destination. The adapter supplies exact occurrence
metadata and delivery instructions separately to each authorized executor run,
including continuations, from the occurrence's immutable snapshot. Do not copy
tool instructions, identifiers or the team's operating manual into the brief.
Times are explicitly Europe/Moscow; native UTC cron expressions also shift weekdays.
The tool pins the team, private Notion research destination and owner recipient.

`recurring_tasks list` shows actual native job IDs, next runs and logical delivery
time; `status` shows editions and errors. `configure` updates the same ID;
`pause/resume` controls future fires, not an already active Linear task. Existing
occurrences retain their original brief, period and destination after edits.

When the owner wants reports only for significant findings, set `skip_empty: true`
on the existing definition (`recurring-no-updates-v1`). Omitted policy on a later
tool edit preserves the saved choice. Existing definitions default to producing
a report. Each substantive edition has one reader-facing Notion page shared by
authors and editors; internal source checks belong in handoffs or a working archive.

The owner dashboard's `/cron?profile=default` lists both managed records: a
preparation trigger and a five-minute delivery/recovery check. The check uses no
LLM and stays silent before the deadline. Edit these records through
`recurring_tasks`, not independently in native Cron. Generic personal reminders
continue to use Hermes's native cron tool.

## Delivery and failure behavior

A stable occurrence key and deterministic Linear issue UUID fence duplicate ticks.
Lost issue-create responses are reconciled by reading the same issue. Admission
retries are bounded to eight attempts and two hours after the delivery deadline.
Existing delegation, execution limits and task authorization remain in force.

Qui-Gon submits the reviewed Notion page, exact revision and short summary through
`recurring_result`. At delivery time the script verifies the page's revision,
parent and occurrence marker. The native delivery queue sends its output. Only a
confirmed native receipt marks the edition delivered; ambiguous sends are not
blindly replayed. A confirmed delivery triggers one coordinator continuation for
acceptance under the normal low-risk/owner-reserved policy.

For an explicitly configured empty-report policy, a completed check with no
significant findings uses `recurring_result` with `outcome: no_updates`, a concise
`summary` and bounded `verification`, without `page` or `revision`. Only the
authorized coordinator can submit it after task review and finished specialist
work. The terminal result stays in the recurring record: no page, no Telegram
edition, no overdue alert. Normal acceptance still applies, with one coordinator
continuation using the task's research evidence rather than a publication link.
Inaccessible sources or incomplete work must not be relabeled as no news. A
material blocker still gets an actionable failure notice. Once report delivery
is claimed, an empty result cannot replace it; reconcile that send first.

If the report is not ready, send one failure status and create an idempotent
Linear follow-up with a concrete next action. A verified late report may be sent
once within two hours, explicitly marked delayed. Optional missing Telegram data
is a coverage gap, not a reason to fabricate news or skip public-source research.
Set `cron.wrap_response: false` in the native profile to deliver the prepared
message without Hermes's English job-name/ID header and management footer.
This affects presentation only; schedules and native delivery receipts remain intact.
Technical script failures stay in Cron; the host monitor reports unacknowledged
missed editions after the delivery deadline plus five minutes. `doctor` reports
expected, overdue and unreported editions independently of container health.

The receipt provides evidence of transport success, not that the owner read it.
This design does not promise exactly-once behavior for an unknowable remote send.

Linear may return `creator: null` for an automatically delegated agent session.
Scheduled work accepts this only for an existing owner-granted occurrence whose
issue creator, delegate, team and session identity match the installed app.
It does not authorize ordinary creatorless sessions or non-owner continuations.

## Existing travel jobs

When migrating, pause the existing research/delivery jobs under the operator
maintenance lock and retain their IDs/history. Configure the logical definition
with those two IDs; remove old native-Kanban prompts, `context_from` and host-venv
interpreter paths. Verify copied state and actual saved fields before enabling.
Do not leave the old and new schedules enabled together. Use the consistent
pre-cutover snapshot to roll back the runtime, grants and schedules together.

See [incident evidence](recurring-jobs-review.md) and
[optional Telegram collector](telegram-monitor.md).
