# MDA connections setup

The credentials this deployment reads from [MDA connections](https://docs.langchain.com/langsmith/managed-deep-agents-connections) (LangSmith Agent Auth), and how to create them.
Run every step once for each environment: `staging`, `preview` and `prod`.
Each backend setting is off until set, and unset keeps that credential on the backend, as before.

| Slug | Owner | Read by | Backend setting |
|---|---|---|---|
| none: a person's Agent Auth principal | user | The backend's runs for a person on a channel's thread | `MDA_WORKSPACE_ID`, when not the backend's workspace |
| `mda/github-token` | agent | The GitHub proxy of the sandboxes MDA owns whose thread reaches the whole App installation | `MDA_GITHUB_PROXY_CONNECTION=mda/github-token` |
| `open-swe-exa` | agent | `web_search` | `MDA_EXA_CONNECTION=open-swe-exa` |
| `open-swe-github-<env>` | user | `open_pull_request`, for the requester's own PR | `MDA_GITHUB_USER_CONNECTION=open-swe-github-<env>` |
| `open-swe-mcp-<server>` | agent or user | An MCP server whose Open SWE record names the connection | `connection` on the server's record |

## 0. Prepare the shell

From the repository root, with the environment's deploy key and LangSmith workspace in the shell or `mda/.env` (never print them):

```bash
make mda-cli
```

```bash
export ENV=staging DEPLOYMENT=open-swe-mda-staging
test -n "$LANGSMITH_API_KEY" && test -n "$LANGSMITH_WORKSPACE_ID" && echo ready
```

Find the deployment's id (the "Deploy MDA" workflow summary also shows it as `Deployment ID`):

```bash
export DEPLOYMENT_ID=$(curl -fsS "https://api.host.langchain.com/v2/deployments?name_contains=$DEPLOYMENT" \
  -H "X-Api-Key: $LANGSMITH_API_KEY" -H "X-Tenant-Id: $LANGSMITH_WORKSPACE_ID" \
  | jq -r --arg n "$DEPLOYMENT" '.resources[] | select(.name == $n) | .id')
test -n "$DEPLOYMENT_ID" && echo "$DEPLOYMENT_ID"
```

An agent-owned credential belongs to one deployment, so each agent-owned `create` below passes `--deployment-id`.
Without it the CLI looks for a deployment named after the agent, `open-swe`, which does not exist.

## 1. The person's principal

The backend resolves each person's Agent Auth principal in the deployment's workspace with `MDA_RUNTIME_API_KEY`, so a dashboard follow-up on a channel's thread reads the person's user connections.
Set `MDA_WORKSPACE_ID` on the backend to the workspace id the deployment runs in (`$LANGSMITH_WORKSPACE_ID`) only when it is not the backend's own.
The key needs permission to manage the workspace's integrations or to create deployments; without it, such a follow-up reads none of the person's user connections.

## 2. `mda/github-token`: nothing to create

The GitHub channel's `mda deploy` keeps the GitHub App's installation token under this reserved slug, and Agent Auth renews it.
Deploy this change first: `mda/channels/github.py` now asks for the App's repository permissions, and deploy renews the token with them.
Check the deploy report lists contents, pull requests, issues, checks and workflows as write, and statuses, security events and actions as read; an installation that has not granted all of them keeps the old token.
Then check Agent Auth holds the deployment's App, which it stores as `mda/github-app/<deployment id>`:

```bash
uv run --project mda --locked mda connections list --project mda --json \
  | jq -e --arg s "mda/github-app/$DEPLOYMENT_ID" 'any(.[]; .slug == $s)'
```

Only then set `MDA_GITHUB_PROXY_CONNECTION=mda/github-token` on the backend: a deployment that runs an older build leaves those sandboxes without a proxy config.

## 3. `open-swe-exa`: the Exa key for `web_search`

With `EXA_API_KEY` in the shell or `mda/.env`:

```bash
uv run --project mda --locked mda connections create open-swe-exa \
  --project mda --deployment-id "$DEPLOYMENT_ID" --secret-from-env EXA_API_KEY
```

Then set `MDA_EXA_CONNECTION=open-swe-exa` on the backend.
The backend keeps `EXA_API_KEY` for Open SWE's own MCP server.

## 4. `open-swe-github-<env>`: each person's GitHub, for the PRs they open

The environment's GitHub App is the OAuth client, so a person grants Open SWE's own App.
With the App's `GITHUB_APP_CLIENT_ID` and `GITHUB_APP_CLIENT_SECRET` in the shell or `mda/.env`:

```bash
uv run --project mda --locked mda connections create "open-swe-github-$ENV" \
  --project mda --oauth github \
  --client-id "$GITHUB_APP_CLIENT_ID" --secret-from-env GITHUB_APP_CLIENT_SECRET
```

Add the redirect URI it prints (for example `https://api.smith.langchain.com/v1/agent-auth/oauth/callback`) to the GitHub App's **Callback URL** list, next to the dashboard's.
A person who has not granted it is asked to connect GitHub the first time a run opens a PR for them.
Only Slack can show that request today: a GitHub or Linear run links its pause to LangSmith Studio, which cannot resume with a person's grant, so the run stays paused.
Set `MDA_GITHUB_USER_CONNECTION=open-swe-github-$ENV` on the backend only once those channels can ask, or where Slack is the only channel.

## 5. `open-swe-mcp-<server>`: MCP servers on connections

Do this for each MCP server whose credential should leave Open SWE's database.
A server with a fixed key (instance or workspace tier, `"owner": "agent"`):

```bash
uv run --project mda --locked mda connections create open-swe-mcp-incident-io \
  --project mda --deployment-id "$DEPLOYMENT_ID" --secret-from-env INCIDENT_IO_API_KEY
```

A server that registers OAuth clients itself, where each person grants their own account (personal tier, `"owner": "user"`):

```bash
uv run --project mda --locked mda connections create open-swe-mcp-notion --project mda --mcp https://mcp.notion.com/mcp
```

Then save the server's record with the connection and the tools to allow, through the dashboard API as an admin (`PUT /dashboard/api/mcps/<name>`, `/dashboard/api/workspaces/<workspace>/mcps/<name>`, or `/dashboard/api/my-mcps/<name>` for a person's own):

```json
{
  "name": "incident",
  "url": "https://mcp.incident.io/mcp",
  "allowed_tools": ["incident_list"],
  "connection": {"slug": "open-swe-mcp-incident-io", "owner": "agent"}
}
```

The credential goes as `Authorization: Bearer <credential>`, or as it is in the header `connection_header` names, such as `DD_API_KEY`.
The dashboard's forms do not set `connection` yet.

## 6. Verify

```bash
uv run --project mda --locked mda connections list --project mda --json | jq -r '.[].slug' | grep -E '^open-swe-'
```

`list` shows the workspace's slugs, not which deployment holds a credential.
A run whose tool says `no agent connection is set for slug '<slug>'` means this deployment has none: repeat that slug's step with this environment's `DEPLOYMENT_ID`.

## Local development

`mda dev` reads an agent-owned connection from `mda/.env` as `MDA_DEV_<SLUG>`, with `-` as `_` and `/` as `__`: `MDA_DEV_OPEN_SWE_EXA` and `MDA_DEV_MDA__GITHUB_TOKEN` (`gh auth token` works).
User-owned connections need a deployed agent.
