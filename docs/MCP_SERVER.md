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

## Who can use it

Every call verifies that the token's GitHub login can access the PR's repository, using the same check as the dashboard's review routes (`require_repo_access_for_user`, which uses that user's own stored GitHub token). Consequences:

- A user must have **signed in to the dashboard with GitHub at least once**. Otherwise calls return `login_required`.
- No access returns `forbidden`. If access can't be verified (GitHub error, etc.) the call is refused with `access_check_failed`.
- Treat bearer tokens as secrets: whoever holds one can request reviews with that user's repo access.

## Mint a token

Tokens are bound to a **GitHub login**, which is the identity passed to the reviewer run (same as the dashboard's review button).

```bash
python -m agent.mcp_server.auth mint octocat --days 30
```

## Connect Claude Code

```bash
claude mcp add --transport http open-swe https://<deployment>/integrations/mcp \
  --header "Authorization: Bearer $OPEN_SWE_MCP_TOKEN"
```

Then ask: *"Request an Open SWE review of https://github.com/acme/widgets/pull/42 and fix anything high severity."*

Reviews can take several minutes. Either raise Claude Code's tool timeout (`MCP_TOOL_TIMEOUT`, in ms) or have the agent call `request_review` with `wait: false` and then `get_review` until `status` is `completed`.

## Tools

- `request_review(pr_url, wait=true, timeout_seconds=600)`: starts a review through the same function the dashboard and Slack use (`trigger_pr_review_from_ref`), so the review is also published to the PR on GitHub and appears in the dashboard. If a review of that PR is already running it attaches to it rather than interrupting it.
- `get_review(pr_url)`: current status and findings for the PR's review thread.

Both return `{thread_id, run_id, web_url, status, findings?, error?}` as `structuredContent` (and as JSON text for clients that only read `content`). Each finding has `id, severity, confidence, category, title, file, start_line, end_line, description, suggestion, status, in_diff`. Findings persist across re-reviews, so filter on `status`.

## Quick check with curl

```bash
curl -s https://<deployment>/integrations/mcp \
  -H "Authorization: Bearer $OPEN_SWE_MCP_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
```

## Known limitations

1. **Run source.** Runs are dispatched with `source="dashboard"`, the proven path for a user-triggered review. A dedicated `mcp` source would need handling in `agent/github/token.py`, `agent/utils/thread_participants.py` and `agent/completion.py`.
2. **Tokens.** HMAC-signed, expiring, no per-token revocation (rotate `MCP_TOKEN_SECRET` to revoke all). A dashboard "create token" action and a revocation list are follow-ups.
3. **User mapping.** `resolve_caller` in `auth.py` does not consult the user mapping; repo access is enforced per request instead.
4. **No rate limiting** yet.
