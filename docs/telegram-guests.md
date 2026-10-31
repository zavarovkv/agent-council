# Telegram guest calls

Guest Mode lets someone invoke the bot in a private conversation or group without
adding it as a member. In a supported, up-to-date Telegram client, mention its
username in a text message, for example `@your_bot explain this`, or invoke it
while replying to a message. Telegram may show a dedicated invocation control;
simply typing the name in clients without Guest Mode support is insufficient.
The response is visibly from the bot and is shared with the conversation.

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
healthy worker image and its `telegram-guests-v1` capability. It verifies a copied
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
stopping services. Use current compatible images and post-enable backups when
recovering a replacement host. Do not remove the policy to bypass this check.

## Permissions, limits and storage

- Every guest, including the owner and spouse, uses the same public, tool-free
  policy in [instructions/guest/POLICY.md](../instructions/guest/POLICY.md).
  Personal requests belong in the direct private conversation with the bot.
  Private prompts, reply skills, memory, tools and external integrations are not
  supplied to guest inference. Replies never use the owner's business connection.
- Identity comes from Telegram's numeric sender ID. Bot senders, anonymous/channel
  senders, missing identities and malformed updates are rejected. Usernames do not
  grant rights. Guest contexts are separate from business and public-DM contexts.
- Text only: invocation up to 4,000 characters, one quote up to 2,000 characters,
  response up to 3,000 characters. Media receives a local text-only explanation.
  Links do not trigger browsing or previews. Quoted instructions are untrusted.
- Admission: one pending call per actor, four per chat, 16 globally; 20 calls per
  chat/hour, 10 calls per actor/hour across
  all chats, 60 globally/hour including local notices. Guest inference shares the
  public-DM slot and 40-generation/hour budget. Business retains its own slot and
  60-generation/hour budget. Over-limit calls are dropped without provider spend.
- A local 120-second processing deadline bounds queued work; this is not a
  Telegram API lifetime guarantee. Only `answerGuestQuery` is used. Duplicate,
  expired, interrupted or ambiguously delivered requests are never retried and
  never fall back to `sendMessage`. A new invocation is required.
- Private `plugin-data/council-secretary/guest-queue.sqlite3` retains raw invocation,
  quote and reply token only while pending; it scrubs them at completion/rejection
  or expiry (30-second housekeeping). Deduplication/rate-limit metadata lasts one
  hour. The worker stores no guest question or answer text, only hashed reservations
  for one hour, pruned at startup/next request; aggregate outcome records last 14 days.
  Provider-side retention follows that model account's settings. Encrypted backups
  can retain earlier queue state until their normal backup retention expires.

All guest updates are intercepted before private handlers even when replies are
disabled or settings are invalid. The worker cannot call the private agent.
Existing chats, private authentication and unrelated services are unchanged.

## Verify

`make test` checks identity, revocation, expiry, quotas, deduplication, ambiguous
sends and stateless inference. `make test-integration` tests actual pinned PTB
decoding, dispatcher ordering and `answerGuestQuery` serialization without network.

After deployment, use two real accounts and a group where the bot is not a member.
Invoke it as owner and as another person, including a reply/quote. Each valid
invocation should get one bot reply. Verify that an unrelated later message gets
none, private commands cannot run, and disabling the feature suppresses replies.
`doctor`/monitoring report aggregate queue status without message bodies or tokens.

Official references: [Guest bots](https://core.telegram.org/bots/features#guest-bots),
[answerGuestQuery](https://core.telegram.org/bots/api#answerguestquery),
[python-telegram-bot](https://docs.python-telegram-bot.org/en/stable/telegram.bot.html).
