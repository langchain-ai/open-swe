Read or change a workspace bot: the LangSmith Managed Tools tool kit whose tools every run in the workspace loads, called with the LMT profile Open SWE keeps for the workspace.
Requires a currently authorized workspace admin on a private admin surface.
Authorization is rechecked on every call.
Use `add_server` or `set_kit` only for an explicit request to change the workspace bot.

- `action`: `read`, `add_server` or `set_kit`.
- `workspace`: the workspace's slug; it must already exist.
- `server`: for `add_server` only, the server to register in LangSmith for the whole LangSmith workspace: `name`, its HTTPS `url`, and `kind`: `oauth` for one people sign in to, `secret` for one called with an API key in `header`, or `none` for a public one.
  A public server joins the bot's kit at once; the others join it once an admin connects them for the bot.
- `kit_id`: for `set_kit` only, an existing LMT tool kit's ID for the bot, or null to turn the bot off.

The result's `servers` are those in the bot's kit; `missing` names the ones the bot cannot call yet, whose tools do not load.
Only a workspace admin can connect a service for the bot or choose which of its tools the kit allows, from the workspace's MCP servers in the dashboard; never offer a person's own connect card for it.
