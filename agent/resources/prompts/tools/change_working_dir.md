Switch the current sandbox working directory for all subsequent `execute` and `background_execute` commands in this thread. Pass an absolute directory path. Prefer this over prefixing each command with `cd`.

If that directory has an `AGENTS.md`, its complete contents are returned in this tool's response as append-only context. Follow those instructions for work under that directory. The tool does not edit prior system messages or rewrite conversation history. An invalid path or unreadable instructions leave the previous working directory unchanged.
