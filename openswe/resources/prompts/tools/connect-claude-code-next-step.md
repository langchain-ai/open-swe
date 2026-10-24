REQUIRED: install `settings` now; the token in it is shown only this once.

1. Write `settings` to a file only the person can read (`umask 077`), without printing it.
2. Merge it into `~/.claude/settings.json`, creating the file as `{}` if it does not exist. For each event in `settings.hooks`, first remove any hook handler whose `url` ends with `/dashboard/api/claude-code/hooks` (an earlier connection), then add the new groups. Keep every other key and every other hook as it is.
3. Check the file is still valid JSON and that all eight events point at the new URL, printing only event names and URLs, never the headers. Then delete the temporary file.
4. Tell the person their sessions now mirror to Open SWE, that sessions already open may need restarting to pick up the hooks, and that they can revoke this machine under Settings → Claude Code.
