# Linear

For native agents that receive delegation, @mentions and sub-issues inside
Linear, see [Linear agents](linear-agents.md). The MCP connection below lets the
personal assistant operate Linear; it does not create a visible agent identity.

Connect the personal coordinator to Linear through Hermes' built-in `linear`
catalog entry and [Linear's official MCP](https://linear.app/docs/mcp).
The existing pinned Hermes image includes the client: no extra container,
package, API proxy or custom OAuth application is required.

## Connect

Open the dashboard's **MCP** page in the default profile. Choose **Linear** from
the connection catalog and connect it, or authenticate an already prepared
`linear` connection. Sign in to the intended Linear workspace and review its
OAuth permissions. Hermes handles the callback at
`/api/mcp/oauth/callback/linear`, token storage and refresh. Never send tokens,
authorization codes or API keys through chat or commit them to Git.

For this deployment: [MCP settings](https://council.zavarov.com/mcp?profile=default).

An operator may prepare the catalog configuration disabled before consent.
[`prepare-linear.py`](../scripts/prepare-linear.py) creates that entry from the
pinned native catalog, preserves other connections and leaves existing matching
Linear settings unchanged. Run it as the coordinator user against a copied
profile first, under the host maintenance lock and with a consistent checkpoint.
Leave it disabled until authentication succeeds and tools have been selected.
Use an explicit tool include list for issue search/read/create/update, comments
and the required team/project/status metadata; new server tools should not
automatically expand the selected set. Preserve any existing selection when
reconnecting. Verify a real read from the intended workspace before declaring
the integration ready. Do not create test issues in a production team.

Terminal installations can use `hermes mcp install linear` and
`hermes mcp configure linear` through an interactive runtime-user session.
For a remote Docker deployment, prefer dashboard OAuth so the browser callback
uses the public HTTPS origin instead of a container's loopback address.

## Use and boundaries

Ask the personal assistant to find an issue, create a task in an explicitly
named team/project, or update a specified issue. It should resolve ambiguous
targets first, preserve unrelated fields, read changes back and return the
Linear issue identifier/link. Retry an uncertain create only after checking
whether the issue already exists.

The Telegram public/business secretary remains tool-free. Dashboard operators
and specialist profiles share a trusted runtime; profile names and instructions
are not tenant isolation. Review the OAuth workspace scope before connecting.
For a separate read-only integration, Linear also provides `/mcp/readonly` and
the `read` OAuth scope; changing an existing grant requires explicit setup.

Linear tracks external work; Hermes Kanban tracks agent execution, and Notion
stores reviewed research. Add links between records when requested. Connecting
MCP alone does not implement automatic two-way synchronization or start agents
from arbitrary Linear comments or assignments.

The connection settings and renewable OAuth grant live in the persistent Hermes
profile, outside the checkout and image. Existing encrypted data backups cover
them. Removing the connection disables its tools; revoke the authorization in
Linear too when its account access is no longer wanted.
