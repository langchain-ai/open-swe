# Open SWE MCP server

Lets external agents (Claude Code, Cursor, any MCP client) request PR reviews and read structured findings back.

## Enable

Set on the deployment:

| Variable | Purpose |
| --- | --- |
| `MCP_SERVER_ENABLED=true` | Turns the endpoint on. Off by default (404). |
| `MCP_TOKEN_SECRET` | ≥32 random characters. Signs bearer tokens. Rotating it revokes all tokens. |
| `MCP_ROUTE_PATH` | Optional. Defaults to `/integrations/mcp` so it doesn't collide with LangGraph Platform's own `/mcp`. |
| `MCP_ALLOWED_ORIGINS` | Optional. Comma-separated browser origins allowed to call the endpoint. Normally empty. |
| `MCP_GITHUB_HOSTS` | Optional. Extra PR hosts (GitHub Enterprise Server). |

`ALLOWED_GITHUB_ORGS` and `ALLOWED_GITHUB_REPOS` apply here exactly as they do for GitHub and Slack triggers.

Register the router in `agent/webapp.py` next to the other routers:

```python
from agent.mcp_server import router as mcp_router

app.include_router(mcp_router)
```

## Mint a token

```bash
python -m agent.mcp_server.auth mint alice@corp.com --days 30
```

## Connect Claude Code

```bash
claude mcp add --transport http open-swe https://<deployment>/integrations/mcp \
  --header "Authorization: Bearer $OPEN_SWE_MCP_TOKEN"
```

Then ask: *"Request an Open SWE review of https://github.com/acme/widgets/pull/42 and fix anything high severity."*

Reviews can take several minutes. Either raise Claude Code's tool timeout (`MCP_TOOL_TIMEOUT`, in ms) or have the agent call `request_review` with `wait: false` and then `get_review` until `status` is `completed`.

## Tools

- `request_review(pr_url, wait=true, timeout_seconds=600)`
- `get_review(thread_id)`

Both return `{thread_id, run_id, web_url, status, findings?, error?}` as `structuredContent` (and as JSON text for clients that only read `content`).

## Quick check with curl

```bash
curl -s https://<deployment>/integrations/mcp \
  -H "Authorization: Bearer $OPEN_SWE_MCP_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
```

## Integration points to review

Two functions in `agent/mcp_server/reviews.py` encode assumptions about the reviewer and should be checked against the Slack `review` trigger:

- `start_review`: the run input and `config.configurable` keys. Prefer calling the shared dispatch helper if one exists.
- `extract_findings`: where structured findings are read from (thread state `findings` today).

And in `auth.py`, `resolve_caller` should call the shared user resolver so MCP callers map to users exactly as Slack callers do.
