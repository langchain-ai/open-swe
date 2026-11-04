Read or set a workspace bot: the LangSmith Managed Tools kit whose tools every run in the workspace loads, and the LMT profile whose credentials they run with.
Requires a currently authorized workspace admin.
`read` requires a private admin surface; `set` also works in a verified sole-writer shared thread, returning only an acknowledgement.
Authorization is rechecked on every call.
Use only for an explicit request to change the workspace bot.

- `action`: `read` or `set`.
- `workspace`: the workspace's slug; it must already exist.
- `settings`: for `set`, either or both of `kit_id` and `profile_id`, the LMT kit and profile IDs; null clears one, which turns the bot off.
  Omit `settings` to read.

The bot needs both a kit and a profile.
The result's `services` say which of the kit's services the profile has connected; tools of unconnected ones do not load.
Only a workspace admin can connect a service for the bot, from the workspace's page in the dashboard or in LangSmith; never offer a person's own connect card for it.
