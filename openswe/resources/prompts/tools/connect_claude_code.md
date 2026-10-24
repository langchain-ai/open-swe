Connect the machine this Claude Code session runs on to Open SWE, so every Claude Code session on it is mirrored as a private, read-only Open SWE thread that updates while the session runs.

Pass `machine_name`, a name the person will recognize in Open SWE's Settings → Claude Code, such as the computer's name (`scutil --get ComputerName` on macOS, `hostname` elsewhere). Connecting a name that is already connected replaces that machine's token, so calling this again to reconnect is safe.

It returns `settings`: the `hooks` block for `~/.claude/settings.json`, holding the machine's new token, which is shown only this once. Follow `next_step` to install it. The token is a credential: never print it, paste it into a message, or write it anywhere but that settings file.
