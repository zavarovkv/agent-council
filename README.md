# Agent Council

A self-hosted platform for managing, coordinating and monitoring AI agents.
The project is in early development, built around
[Hermes Agent](https://github.com/NousResearch/hermes-agent).

## What it does

- Runs a personal agent with a web dashboard and Telegram messaging.
- Delegates competitor research to a specialist profile, with the assistant
  reviewing and returning its results.
- Supports ChatGPT / Codex model login and explicit Notion / Google Drive setup.
- Connects to Linear through its official OAuth-backed MCP integration.
- Provides an optional Telegram secretary for selected account chats and public
  bot conversations, with separate reply styles for spouse, contacts and new users.
- Supports opt-in, owner-only [Telegram guest calls](docs/telegram-guests.md) in other chats,
  with stateless public answers and no private tools or history.
- Includes Docker deployment, persistent storage, verified rollback and encrypted
  Cloudflare R2 backup tooling.

## How it works

Docker Compose runs the private Hermes runtime behind HTTPS and Telegram sign-in.
The owner and explicitly invited dashboard operators share its tools, credentials
and history. Specialist profiles use that same trusted runtime.

External correspondence goes through a separate **tool-free secretary** with its
own model session and history. It cannot access the personal agent's files,
Notion, Drive or administrative commands. Conversation styles grant no extra
permissions. Separate runtimes for untrusted full agents are not implemented.
See [architecture](docs/architecture.md) and [security](SECURITY.md).

## Deploy

The [deployment guide](docs/deployment.md) covers the current Ubuntu setup.
It requires Docker Engine 29+, Compose 2.30+, Python 3.11+ and an HTTPS hostname.

1. Configure dedicated external data directories and install the deployment.
2. Set up Telegram login and explicitly identify the owner.
3. Authorize the model account; connect file services and the secretary as needed.

Credentials, sessions, memory and generated skills stay outside Git and the image.
Updates build a versioned image, create a consistent restore point and check copied
state before switching releases. Off-server backups require separate
[R2 configuration and a recovery check](docs/backups.md).

## Instructions and skills

Start with the [instruction index](instructions/README.md). Shared instructions
are grouped by role: `assistant/`, `dexter/`, `secretary/` and `guest/`. The secretary's
common policy and three reply scenarios are editable Markdown files.

Personal rules can live in a [private instruction repository](docs/private-instructions.md),
installed by reviewed commit without including private Git access in Docker or CI.

Commit and deploy secretary changes to apply them. Researcher instructions use
an explicit profile installer that preserves manual edits. Private/generated
Hermes skills remain in persistent storage. [AGENTS.md](AGENTS.md) contains the
repository's development rules.

## Develop

Use Python 3.11+. Docker checks need Compose 2.30+; frontend checks use Node 24.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
make test lint
```

CI also checks the pinned Hermes adapter, container isolation, backup recovery
and the Telegram login flow in a browser using fixture data. See
[development](docs/development.md), [contributing](CONTRIBUTING.md) and the
[technical review](docs/technical-review.md).

See the [architecture review and priorities](docs/architecture-review.md) for
operational follow-up, and [monitoring](docs/monitoring.md) for owner alerts and
independent HTTPS checks.

[Research workflow](docs/research.md) connects native task/run tracking to reviewed
Notion reports and their parent-page index.

[Linear setup](docs/linear.md) connects the personal assistant to issues and projects.

[MIT](LICENSE). Dependencies retain their own licenses.
