# Agent Council

A self-hosted platform for managing, coordinating and monitoring AI agents.
The project is in early development, built around
[Hermes Agent](https://github.com/NousResearch/hermes-agent).

## What it does

- Runs a personal agent with a web dashboard and Telegram messaging.
- Coordinates Квай-Гон, Декстер, Лэндо and Падме through Linear tasks, specialist
  sub-issues, clarification and reviewed deliverables.
- Supports optional [Telegram source collection](docs/telegram-monitor.md)
  for native agent research workflows.
- Supports ChatGPT / Codex model login and explicit Notion / Google Drive setup.
- Connects to Linear through its official OAuth-backed MCP integration.
- Supports isolated Linear specialists for product research, marketing/SEO and editing,
  with a live agent catalog, delegated sub-issues and reviewed local skills.
- Provides an optional Telegram secretary for selected account chats and public
  bot conversations, with separate reply styles for spouse, contacts and new users.
- Supports opt-in, owner-only [Telegram guest calls](docs/telegram-guests.md) in other chats,
  with the owner's private agent and tools when explicitly enabled; the answer
  remains visible to the conversation's participants.
- Includes Docker deployment, persistent storage, verified rollback and encrypted
  Cloudflare R2 backup tooling.

## How it works

Docker Compose runs the private Hermes runtime behind HTTPS and Telegram sign-in.
The owner and explicitly invited dashboard operators share its tools, credentials
and history. Native specialist profiles share this trusted runtime; the four
Linear executors have separate containers, state and task-scoped tools. A shared
workspace service gives trusted agents access to the selected Notion subtree and
Drive folder without copying cloud credentials to every executor.

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

Start with the [instruction index](instructions/README.md). The public repository
contains setup examples; the private instruction repository owns installed role
rules, shared procedures and generated Linear adapters. The secretary has its
own policy and three conversation styles.

Personal rules can live in a [private instruction repository](docs/private-instructions.md),
installed by reviewed commit without including private Git access in Docker or CI.

The private-repository timer applies a verified revision when work is idle,
checks a copied profile and preserves local drift. Private/generated Hermes skills
remain in persistent storage; export and review runtime edits before committing.
[AGENTS.md](AGENTS.md) contains the repository's development rules.

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

[Research workflow](docs/research.md) documents the legacy native filing path.
New work is tracked in Linear; formatted texts belong in Notion and files in Drive.

The public landing is edited and built with the blog; its
[restricted automatic deployment](docs/council-static-deploy.md) is independent
of agent runtime releases.

[Early-access request capture](docs/waitlist.md) saves explicit Telegram waitlist
requests to a dedicated Notion register, with durable capture and duplicate protection.

[Linear setup](docs/linear.md) connects the personal assistant to issues and projects.
The optional [Linear agents](docs/linear-agents.md) receive delegated issues,
ask for clarification and route sub-issues to isolated executors.

[MIT](LICENSE). Dependencies retain their own licenses.

На публичном лендинге доступны RU/EN и светлая/тёмная тема; выбор сохраняется локально. Портреты — PNG 256×256 с прозрачным внешним фоном, мобильный вход использует штатный Telegram Icon.
