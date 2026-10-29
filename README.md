# Agent Council

A platform for managing, coordinating, and monitoring AI agents.

The project is in early development. Its first integration is a self-hosted
[Hermes Agent](https://github.com/NousResearch/hermes-agent) with a web dashboard.

## Current features

- Docker Compose deployment with persistent agent data and workspaces.
- Telegram sign-in for an owner and explicitly invited collaborators.
- ChatGPT / Codex account setup through Hermes.
- A login page built with [Beauty.css](https://github.com/zavarovkv/beauty-css).

Collaborators share one runtime and the same permissions. Per-user roles and
isolation between untrusted users are not implemented yet.

## Setup

Follow the [deployment guide](docs/deployment.md) for Docker configuration,
Telegram sign-in, model setup, updates and backups. It documents the current
Ubuntu deployment and its server-specific scripts.

Credentials and live agent data stay outside Git and the Docker image.

## Development

Local checks require Python 3.11+. Container integration tests require Docker
and Compose 2.30+. Frontend checks use Node 24.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
make test
make lint RUFF=.venv/bin/ruff
```

See the [development guide](docs/development.md) for container, browser and
live deployment checks.

## Documentation

- [Architecture](docs/architecture.md)
- [Deployment](docs/deployment.md)
- [Contributing](CONTRIBUTING.md)
- [Security](SECURITY.md)
- [Technical review](docs/technical-review.md)

## License

A project license has not been selected yet. Public source availability alone
does not grant an open-source license. Dependencies retain their own licenses.
