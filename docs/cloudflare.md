# Cloudflare MCP

Qui-Gon's native `default` profile connects to the [official Cloudflare API MCP](https://developers.cloudflare.com/agents/model-context-protocol/cloudflare/servers-for-cloudflare/)
at `https://mcp.cloudflare.com/mcp`. Its compact `search`, `execute`, `docs` and
`whoami` tools cover the API without loading thousands of endpoint schemas.
DNS, Workers, R2 and other products remain subject to the account permissions
selected during authorization.

1. Add [the connection template](../config/cloudflare-mcp.json) as
   `mcp_servers.cloudflare` in the native profile's private `config.yaml`.
   Preserve other MCP servers and leave this connection disabled until authorized.
2. Open [Council's MCP settings](https://council.zavarov.com/mcp?profile=default),
   choose **Authenticate** for `cloudflare`, sign into Cloudflare and approve
   the account and permissions that Council needs.
3. Verify the OAuth grant using `whoami` and an actual read of the intended
   account/zone. Enable the connection and reload MCP in an existing conversation.

Hermes handles OAuth, token refresh and the HTTPS callback at
`/api/mcp/oauth/callback/cloudflare`. Credentials stay in Hermes' private OAuth
storage. No API token needs to be pasted into chat; the R2 backup credentials
are not reused for this connection. Merely listing tools does not prove access
to account data, and successful reads do not prove write permissions.

This authorizes the native coordinator, not the isolated Linear executors.
Connecting the MCP does not itself change DNS, deploy Workers, or modify backups.
