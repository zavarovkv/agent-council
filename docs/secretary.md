# Telegram secretary

The same Telegram bot serves two distinct routes. Direct owner messages keep
using the private Hermes agent. Messages received through the owner's Telegram
Chat Automation connection go to a separate, tool-free secretary service.
They never enter the owner's agent session or command/tool dispatcher.

The initial implementation handles **text in selected private chats**. Media,
groups/guest mode, calendar actions and cross-chat actions are not supported.
The secretary answers general questions and clarifies requests; it cannot access
private documents, execute commands or claim to notify the owner. Its prompt
identifies it as the configured owner's digital assistant. Names are deployment
settings, not a repository-owner default.

## Setup

1. Deploy the compatible release using `python3 scripts/manage.py deploy`.
   The feature is disabled by default. This ensures the previous rollback image
   contains the native handler before its configuration is enabled.
2. On the Docker host run:

   ```bash
   sudo python3 scripts/manage.py prepare-secretary --owner-name "Your name" --assistant-name "Assistant name"
   sudo python3 scripts/manage.py deploy
   ```

   Preparation requires a verified numeric owner pin and an unused/managed
   `.council/secretary` directory. It generates a private machine key, enables the
   bundled plugin through Hermes' official CLI and copies only the public model
   choice into a new profile. Owner credentials, memory, sessions, skills and MCP
   configuration are never copied.
3. From your local checkout run:

   ```bash
   bash scripts/setup-model.sh secretary
   ```

   Authenticate this service's **own** model session in your browser. You may use
   your existing provider account, but this creates a separate grant rather than
   exposing the private agent's credentials. No API keys belong in chat or Git.
4. In `@BotFather`, enable **Secretary Mode** for the bot. In your own Telegram
   settings open **Chat Automation** (older clients: Telegram Business → Chatbots),
   select the bot and explicitly choose chats it can manage. Give permission to
   reply. Start with one test conversation. The connecting account must match the
   dashboard's pinned owner ID; another person's connection is rejected.
5. Have the other participant send a new text in that conversation. Verify the
   response arrives through your connection, while ordinary direct messages to
   your bot still use the personal agent.

Server deployment does not turn on Telegram permissions on your behalf. Model
authorization and account connection remain explicit owner steps. HTTP health
proves the worker is running, not that a provider grant or real chat is authorized.

## Editing reply skills

The two editable reply scenarios live in this repository:

- [`agents/secretary/contacts/SKILL.md`](../agents/secretary/contacts/SKILL.md):
  warmer, concise replies to explicitly listed contacts.
- [`agents/secretary/new-users/SKILL.md`](../agents/secretary/new-users/SKILL.md):
  the default introduction and request-clarification scenario for everyone else.

Edit the Markdown below the YAML front matter, commit the change and deploy that
release using `sudo python3 scripts/manage.py deploy` on the Docker host. Docker
ships both files in the image; the worker loads them on startup. Editing a file
in GitHub alone does not change the running release. Do not edit files inside a
running container: those changes are lost on recreation. The secretary does not
use the owner's full Hermes skill loader or execute skill code.

The [Bot API User](https://core.telegram.org/bots/api#user) and
[BusinessConnection](https://core.telegram.org/bots/api#businessconnection) objects
do not expose whether the other person is in the owner's address book. Telegram's
Chat Automation contact filters select which chats reach the bot, but do not label
each incoming message with a contact category. Therefore this release uses an
**explicit secretary contact list**, not automatic address-book synchronization.
It never infers contact status from a username, a claim in a message, or an
earlier conversation. Everyone outside the list keeps the new-user scenario,
including returning correspondents.

On the Docker host, run `sudoedit /etc/agent-council/secretary.env` and add or edit
this line using actual numeric Telegram user IDs, without spaces:

```dotenv
COUNCIL_SECRETARY_CONTACT_IDS=111111111,222222222
```

An absent/empty value means no contacts. Keep this private list out of Git. Leave
the key, owner ID and other settings intact. Run `sudo python3 scripts/manage.py
deploy` from the server checkout to recreate the two Council services with the
updated environment; the existing image is reused for an unchanged Git revision.
This operation preserves model authorization and stored data and creates a
consistent rollback point. A malformed list keeps business updates closed rather
than guessing which scenario to use.

Only the verified bridge chooses the scenario. A recipient cannot change it by
asking the model. Both scenarios have the same tool-free permissions; contact
membership grants no private-agent, document or administrative access. Changing
someone's category starts a separate history context. Existing pre-scenario
history is retained under normal retention, but is not reused in these contexts.
The common safety/identity constraints remain in
`src/secretary_worker/service.py`; the editable files control conversation style
and clarification, not execution permissions. No private facts or credentials
belong in either skill.

## Isolation and delivery

The plugin uses Hermes' official `register_telegram_handler` extension point.
It stops business updates before ordinary handlers, including while unconfigured.
There is one existing Telegram poller. Each new message's connection is fetched
from Telegram and checked for numeric owner identity, enabled status and reply
rights. Rights are checked again before delivery. Sending always includes
`business_connection_id` and the incoming reply anchor, with no fallback to an
ordinary bot message or another chat.

The worker is non-root, with a read-only root, no capabilities, resource limits,
no published ports, no Telegram token and only its own profile mounted. It receives
a fixed schema with an opaque conversation ID, text and a validated scenario
selected by the trusted bridge. Callers cannot supply prompts, models or tools.
Model requests have no tools; returned tool calls and
reasoning-only output are rejected. No agent executor is created. Chats have
separate recent context. The API uses a dedicated random service key.

The service environment is `/etc/agent-council/secretary.env`, root-only. Its
persistent model home is `HERMES_DATA_DIR/.council/secretary`; usual backups include
its separate model grant/history and exclude its software/caches. Backups stop
both services for consistency; dashboard credential/access changes recreate only
Hermes. This directory is deliberately outside Hermes' agent-profile discovery:
the secretary never registers an additional full-agent gateway or Telegram poller.

A private persistent queue tracks replies. Generation resumes idempotently
after restart. Ambiguous sends remain `uncertain` and never automatically replay,
avoiding duplicate messages after a network loss. Failed/interrupted requests
need a new message; this is not exactly-once delivery. Deleting/editing an incoming
message cancels pending versions. A manual owner reply cancels pending replies
and gives the human a five-minute quiet window. Telegram pause/revocation blocks
sending; an already in-flight send cannot be recalled.

Limits: two model calls concurrently, 20 requests per conversation/hour and 100
globally/hour. Input is limited to 6,000 characters; replies to 3,000. History is
pruned after 14 days on startup/new requests, with eight delivered turns used as
context. Stored backups follow their separate retention policy. This is not
a general public agent sandbox: it has no tools or private knowledge integrations.

See [Telegram Chat Automation](https://telegram.org/blog/ai-bot-revolution-11-new-features#chat-automation-in-profiles)
and [Business Bot API](https://core.telegram.org/bots/features#business-bots).
