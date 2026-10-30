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
   `profiles/secretary` directory. It generates a private machine key, enables the
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
a fixed schema with an opaque conversation ID and text. Callers cannot choose
prompts, models or tools. Model requests have no tools; returned tool calls and
reasoning-only output are rejected. No agent executor is created. Chats have
separate recent context. The API uses a dedicated random service key.

The service environment is `/etc/agent-council/secretary.env`, root-only. Its
persistent profile is `HERMES_DATA_DIR/profiles/secretary`; usual backups include
its separate model grant/history and exclude its software/caches. Backups stop
both services for consistency; dashboard credential/access changes recreate only
Hermes.

A private persistent queue tracks replies. Generation resumes idempotently
after restart. Ambiguous sends remain `uncertain` and never automatically replay,
avoiding duplicate messages after a network loss. Failed/interrupted requests
need a new message; this is not exactly-once delivery. Deleting/editing an incoming
message cancels pending versions. A manual owner reply cancels pending replies
and gives the human a five-minute quiet window. Telegram pause/revocation blocks
sending; an already in-flight send cannot be recalled.

Limits: two model calls concurrently, 20 requests per conversation/hour and 100
globally/hour. Input is limited to 6,000 characters; replies to 3,000. History is
retained for up to 14 days, with eight answered turns used as context. This is not
a general public agent sandbox: it has no tools or private knowledge integrations.

See [Telegram Chat Automation](https://telegram.org/blog/ai-bot-revolution-11-new-features#chat-automation-in-profiles)
and [Business Bot API](https://core.telegram.org/bots/features#business-bots).
