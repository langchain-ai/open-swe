Connect this machine's Claude Code to Robin, so every Claude Code session on it is mirrored into a private, read-only Robin thread.
Call it once per machine, with a `machine_name` the person will recognize, such as the hostname.

It returns a one-time `claim` and the `claim_url` to redeem it at, valid for `expires_in_seconds`, not the token itself, so the token never passes through you.
In Claude Code with the `robin` plugin (`robin@robin`), `/robin:connect` does all of this.
Existing users should disable or uninstall `open-swe@open-swe`, remove the old `open-swe` marketplace, then re-add `langchain-ai/open-swe` to register the `robin` marketplace before installing Robin; this avoids duplicate hooks and tools.
Otherwise connect without the plugin like this:

1. POST to `claim_url` with `Authorization: Bearer <claim>` from this machine.
   The response's `settings.hooks` is the full hooks block, with the token inside it.
2. Merge it into `~/.claude/settings.json` in the same command, so the response never reaches you or the terminal: remove every hook whose `url` ends in `/dashboard/api/claude-code/hooks`, append each event's hook groups from `settings.hooks`, keep every other hook and setting, and write the file back.
3. Tell the person to start a new Claude Code session; it appears in the Robin sidebar on its first prompt.

A claim mints one token; redeeming it again fails with 409, and the person revokes tokens under Settings → Claude Code in the dashboard.
