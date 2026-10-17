Record a Claude Code hook event in Open SWE. Claude Code calls this from an `mcp_tool` hook; never call it yourself.

To set it up, add a hook handler like this one under each hook event in the user's Claude Code settings, with `server` set to the name this MCP server has there:

```json
{
  "type": "mcp_tool",
  "server": "oswe",
  "tool": "record_hook_event",
  "input": {
    "event": {
      "hook_event_name": "${hook_event_name}",
      "session_id": "${session_id}",
      "transcript_path": "${transcript_path}",
      "cwd": "${cwd}",
      "tool_name": "${tool_name}",
      "tool_input": "${tool_input}"
    }
  }
}
```

`event` holds one `"<field>": "${<field>}"` entry per hook input field to forward. Claude Code sends a missing field as an empty string and an object or array field as a JSON string. The tool returns an empty result, so it never blocks the action.
