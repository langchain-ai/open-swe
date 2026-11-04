Read a workspace bot's LangSmith Managed Tools services, or register a new MCP server for it.
The bot is the workspace's LMT profile: every run in the workspace loads every service it has connected.
Requires a currently authorized workspace admin on a private admin surface.
Authorization is rechecked on every call.
Use `add_server` only for an explicit request to add a server.

- `action`: `read` or `add_server`.
- `workspace`: the workspace's slug; it must already exist.
- `server`: for `add_server`, the server to register in LangSmith for the whole LangSmith workspace: `name`, its HTTPS `url`, and `kind`, `oauth` for one people sign in to or `secret` for one called with an API key, with the `header` the key goes in.
  Omit it to read.

The result's `services` say which services the bot has connected; tools of unconnected ones do not load.
A new server is not connected yet.
Only a workspace admin can connect a service for the bot, from the workspace's page in the dashboard, where Open SWE makes the bot's LMT profile on the first connect; never offer a person's own connect card for it.
