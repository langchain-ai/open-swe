---
description: Connect this machine's Claude Code to Robin, so every session is mirrored into a private Robin thread.
argument-hint: "[machine name]"
disable-model-invocation: true
allowed-tools: Bash(hostname) mcp__plugin_robin_robin__connect_claude_code Bash(python3 ${CLAUDE_SKILL_DIR}/connect.py *)
---

1. Call `connect_claude_code` on the Robin MCP server with `machine_name` set to `$ARGUMENTS`, or to the output of `hostname` when that is empty.
1. Run `python3 ${CLAUDE_SKILL_DIR}/connect.py <claim_url> <claim>` with the `claim_url` and `claim` it returned.
   The script redeems the claim and writes the hooks into Claude Code's `settings.json` itself, so the token never reaches you; it prints only the outcome.
1. Relay the script's last line.
   When it connected, tell the person to start a new Claude Code session, which appears in the Robin sidebar on its first prompt.
