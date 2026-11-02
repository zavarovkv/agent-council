# GitHub MCP

Qui-Gon's native `default` profile uses the [official GitHub remote MCP](https://github.com/github/github-mcp-server/blob/main/docs/remote-server.md)
at `https://api.githubcopilot.com/mcp/`. Enabled toolsets cover repositories,
issues, pull requests and Actions. There is no local MCP container or Docker socket.

Run from the operator's computer:

```bash
python3 scripts/setup-github.py
```

The script prepares a disabled connection, opens GitHub's fine-grained token
form and asks for the token in a hidden terminal prompt. Choose **Only select
repositories** and include `agent-council`, `agent-council-skills` and `zavarov.com`.
Add other projects if needed. The prefilled permissions are Contents, Issues,
Pull requests, Actions and Workflows: write. Metadata read is implicit. The
token expires after 90 days; rerun setup to rotate it before expiration.

The credential travels over SSH stdin and is stored by Hermes in its private
profile `.env` as `MCP_GITHUB_API_KEY`. `config.yaml` contains only an environment
reference. No deployment key or developer credential is copied to agents.
Setup pins the owner's immutable GitHub ID, checks repository reads, PR lists,
Actions runs and a real MCP file read before saving and enabling the connection.
These checks do not exercise write operations or prove every write permission;
GitHub enforces the actual grant on each call.

Start a new Hermes session after setup, or send `/reload-mcp` in the private
bot chat to refresh an open session. No service restart is needed.

```bash
python3 scripts/setup-github.py --verify
# Optionally specify a different verification set (does not change GitHub permissions):
python3 scripts/setup-github.py --repository zavarovkv/agent-council
```

`--prepare` only installs a disabled configuration and preserves an existing
matching connection. Different existing settings require explicit reconciliation.
Private pre-change copies of `.env` and `config.yaml` are kept beneath
`HERMES_HOME/.council/github-setup/`; setup uses the shared operations lock.

This connects the native coordinator. Isolated Linear executors deliberately
do not inherit its MCP credentials. Their GitHub access needs its own credential
boundary; do not claim that authorizing the native profile authorizes all agents.
Token permissions restrict writes/private repositories; public repositories remain
readable as described in [GitHub's token documentation](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens).
Remote OAuth requires a registered client application and is not a one-click
dynamic registration flow for an arbitrary Hermes installation.
