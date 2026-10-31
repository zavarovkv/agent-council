# Telegram guest calls

Guest Mode lets the verified owner invoke the bot in a private conversation or group without
adding it as a member. In a supported, up-to-date Telegram client, mention its
username in a text message, for example `@your_bot explain this`, or invoke it
while replying to a message. Telegram may show a dedicated invocation control;
simply typing the name in clients without Guest Mode support is insufficient.
The response is visibly from the bot and is shared with the conversation. Other
participants cannot invoke it: the gateway checks the pinned numeric owner ID
before admission and again before generation/delivery. A username, forwarded name
or quote cannot grant access. Others replying to a guest answer are ignored too.

The bot receives only that invocation and, when Telegram supplies it, one quoted
or replied-to message. It cannot read the chat history or answer future messages
without another invocation. This is separate from inline search, ordinary bot DMs
and the owner's Secretary Mode connection.

## Enable or revoke

First deploy this release and complete the [isolated secretary setup](secretary.md),
including its own model login. Guest replies default to **off**. Enable Guest Mode
for the same bot in BotFather, then run on the Docker host:

```bash
sudo python3 scripts/manage.py guest-mode
sudo python3 scripts/manage.py guest-mode --enable
```

The command verifies `getMe.supports_guest_queries`, the pinned owner, a matching
healthy worker image and its `telegram-guests-v1` and `telegram-guest-owner-v1`
capabilities. It verifies a copied
profile before atomically installing a private policy epoch. No token belongs in
the command line. The feature does not start another poller or change the native
adapter's `allowed_updates=Update.ALL_TYPES` setting.

```bash
sudo python3 scripts/manage.py guest-mode --disable
```

Revocation needs no model, network call, idle queue or restart. Queued/generated
answers from the old epoch cannot be sent after the next policy check. A request
already handed to Telegram cannot be recalled. The disabled policy file remains:
deployment and restore reject images without the guest interception guard before
stopping services. Schema-2 policy files also require the owner-only guard, so an
older public-guest image cannot reopen access through a rollback. Existing
schema-1 policies load with owner-only admission in the new image and migrate
on the next operator change. Use current compatible images and post-enable backups when
recovering a replacement host. Do not remove the policy to bypass this check.

## Extended owner assistant

Enable the main profile after deploying an image with `telegram-guest-agent-v1`:

```bash
sudo python3 scripts/manage.py guest-mode --enable --owner-agent
```

This writes schema 3 after a copied-profile smoke check. Rollback/restore must
retain this capability even after disabling Guest Mode. The immutable owner ID
is checked at admission, before native execution, while waiting and before each
Telegram edit. Neither spouse, contacts, usernames nor quotes grant this route.
Ordinary public DMs and Secretary Mode keep their isolated policies.

A call in either a private conversation or a group now uses the main Hermes
profile's memory, skills, model and available tools. A deterministic, separate
native session per owner/chat/thread preserves context between invocations; it
never resumes the direct-DM transcript. Quoted messages are explicitly untrusted.
Answers are visible to the conversation: relevant private context requested by
the owner may be included, but unrelated memory and credentials must not be dumped.
The bot sees the invocation and one quote, not the group's full history.

The bot acknowledges via `answerGuestQuery`, stores its `inline_message_id`, and
edits that response with progress, clarification or the final result. No
`sendMessage` fallback, extra poller, model credential copy or approval bypass.
Execution uses the pinned Hermes gateway's own session and prompt protocol.
Clarification answers can be supplied by the owner with another invocation in
the same conversation. Dangerous-action approvals remain in the Council session.
Substantial delegated tasks are tracked by the assistant in Linear; an accepted
request or draft is not automatically treated as a completed task.

There is one active owner guest run globally, at most 60 accepted calls/hour,
and a 30-minute wait limit. Native interruption is requested on revocation,
timeout or transport failure; already performed tool effects are not rolled back.
Uncertain sends/submissions and started jobs are never automatically replayed
following a restart. The reply tells the owner to inspect Council before retrying.
The native session retains the actual execution history. Raw queue inputs are
scrubbed when the attempt ends, with deduplication metadata retained up to a day
and pruned on the next request in the private 0600 `owner-guest.sqlite3` database.
Encrypted backups follow normal retention. No private material enters the public
landing snapshot.

Without `--owner-agent`, the owner still gets the original stateless, tool-free
public guest policy in `instructions/guest/POLICY.md`. Its queue retains a 120-second
local deadline and lower public inference quotas. Disabling invalidates both
routes immediately at their next check; an HTTP request already sent to Telegram
cannot be recalled. All guest updates remain intercepted, including malformed
updates and updates received while disabled.

## Verify

`make test` checks identity, revocation, expiry, quotas, deduplication, ambiguous
sends and stateless inference. `make test-integration` tests actual pinned PTB
decoding, dispatcher ordering and `answerGuestQuery` serialization without network.

After deployment, use two real accounts and a group where the bot is not a member.
Invoke it as owner, including a reply/quote: each valid invocation should get one
bot reply with correctly rendered emphasis. Try another account, a reply from
another participant and a forged owner name: none should produce a reply or model
call. Verify that an unrelated later message gets none, private commands cannot
run, and disabling the feature suppresses replies.
`doctor`/monitoring report aggregate queue status without message bodies or tokens.

Official references: [Guest bots](https://core.telegram.org/bots/features#guest-bots),
[answerGuestQuery](https://core.telegram.org/bots/api#answerguestquery),
[python-telegram-bot](https://docs.python-telegram-bot.org/en/stable/telegram.bot.html).
