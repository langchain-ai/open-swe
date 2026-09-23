# Installation Guide

This guide deploys Open SWE for a team. To run it on your own machine while developing, use the [development guide](DEVELOPMENT.md) instead.

Open SWE is one deployment: a LangGraph server that runs the graphs (`agent`, `reviewer`, `analyzer`, `chat`, `scheduler`), the FastAPI app (`agent.webapp:app`) that owns the webhooks and the dashboard API, and the web dashboard, served from the same origin at `/`. Webhooks, the dashboard, GitHub login, and the API all share the deployment's URL, so there is no second frontend deploy and no cross-origin cookie or CORS setup.

What a deployment needs:

| Value | How you get it |
|---|---|
| `LANGSMITH_API_KEY` | LangSmith → Settings → API Keys. LangGraph Platform injects it. |
| A model provider key such as `ANTHROPIC_API_KEY`, or `LANGSMITH_GATEWAY_API_KEY` for the LangSmith LLM Gateway | Your provider, or a LangSmith key with `gateway:invoke` (step 4) |
| `GITHUB_APP_ID`, `GITHUB_APP_CLIENT_ID`, `GITHUB_APP_CLIENT_SECRET`, `GITHUB_APP_PRIVATE_KEY`, `GITHUB_WEBHOOK_SECRET`, `GITHUB_APP_INSTALLATION_ID` | The GitHub App you create in step 3 |
| `SLACK_BOT_TOKEN`, `SLACK_SIGNING_SECRET`, `SLACK_APP_ID`, `SLACK_BOT_USER_ID`, `SLACK_BOT_USERNAME` | The Slack app you create in step 5 |
| `TOKEN_ENCRYPTION_KEY`, `DASHBOARD_JWT_SECRET` | Two random secrets you generate (step 6) |
| `ALLOWED_GITHUB_ORGS` or `ALLOWED_GITHUB_USERS` | The GitHub organizations or users allowed to log in (step 6) |
| `CONFIGURED_ADMINS` | The GitHub logins or emails of your admins (step 6) |
| `LANGSMITH_HOST_API_URL` | The deployment's own public URL. LangGraph Platform injects it; standalone deployments must set it. |

GitHub and Slack are the two surfaces every deployment has; Linear is an optional add-on. Every variable Open SWE reads is declared in `agent/config.py` with its description and default; that file is the complete reference.

## 1. Create the deployment

You need the deployment's public URL before the GitHub App can be created, so create the deployment first. Its initial revision may remain stopped until step 6, when you configure the GitHub and Slack variables plus a required login allowlist.

**LangGraph Platform.** Connect the repository to a new deployment in LangSmith → Deployments. The image build bundles the dashboard (the `dockerfile_lines` in `langgraph.json`), so the deployment URL serves the UI at `/` and the API beneath it; a failed UI build is logged and the backend still deploys. The platform injects `LANGSMITH_API_KEY`, `LANGSMITH_TRACING`, `LANGSMITH_PROJECT`, and the deployment-specific `LANGSMITH_HOST_API_URL`. You will set the remaining environment variables in step 6.

**Standalone Docker.** The root `Dockerfile` builds a production LangGraph API server image (not the sandbox image):

```bash
docker build -t open-swe .

docker run \
  --env-file .env \
  -p 8123:8000 \
  --add-host=host.docker.internal:host-gateway \
  -e DATABASE_URI="postgres://postgres:postgres@host.docker.internal:5432/postgres?sslmode=disable" \
  -e REDIS_URI="redis://host.docker.internal:6379" \
  -e LANGGRAPH_AUTH_TYPE="langsmith" \
  -e LANGSMITH_AUTH_ENDPOINT="https://api.smith.langchain.com" \
  -e LANGSMITH_TENANT_ID="<your LangSmith workspace id>" \
  -e LANGSMITH_HOST_API_URL="https://<your-backend-url>" \
  open-swe
```

The example assumes Postgres and Redis run on the Docker host; `--add-host` is what makes `host.docker.internal` resolve on a plain Linux Docker Engine. If they run as containers, drop the flag and point `DATABASE_URI` / `REDIS_URI` at their service names on a shared network. Add the standalone Agent Server requirements: `DATABASE_URI`, `REDIS_URI`, `LANGSMITH_API_KEY`, and `LANGGRAPH_CLOUD_LICENSE_KEY`. Expose port `8000` through your ingress, and do not use scale-to-zero hosting: background runs rely on the Redis- and Postgres-backed workers staying up. Bundle the dashboard by building it (`make build-dashboard`) before `docker build`, or set `DASHBOARD_STATIC_DIR` to a directory holding the build.

**Authentication.** The three `LANGGRAPH_AUTH_TYPE` lines matter. The standalone image defaults to `noop`, which leaves the LangGraph API (`/threads`, `/runs`, `/assistants`, `/store`) open to anyone who can reach the port; only the dashboard API (session cookie) and the webhooks (signatures) check anything themselves. `langsmith` makes the LangGraph API require a LangSmith API key from your workspace on every call, which is what LangGraph Platform does and what Open SWE's own calls already send (`LANGSMITH_API_KEY`); the webhooks and dashboard API are custom routes and keep working as before. It needs `LANGSMITH_AUTH_ENDPOINT` (your LangSmith API URL) and `LANGSMITH_TENANT_ID` (the workspace id, shown under **Settings → Workspaces** in LangSmith). Use `noop` only on a private network behind a gateway that does the authentication for you.

Either way, the URL browsers and webhooks use from here on is `<URL>`: `https://<name>-<hash>.<region>.langgraph.app` on the platform, or your ingress hostname in front of the container.

**Analytics storage.** When `POSTGRES_URI` is available to application code, startup migrations create and update the `open_swe_analytics` schema in that database. The database role must be allowed to create the schema and manage its tables and indexes. Workspace identity and collection-start metadata are persisted automatically; no additional analytics settings or worker deployment are required. `POSTGRES_URI` is required: the same migrations create the `repository`, `pull_request`, `pull_request_thread`, and `pull_request_review` tables that record which threads and reviews belong to each pull request, the `users` and `user_identity` tables that give each person one stable id across their GitHub and Slack identities (a user row is only ever created for a GitHub login that passes the same `ALLOWED_GITHUB_USERS`/`ALLOWED_GITHUB_ORGS` gate as dashboard login; when `CONFIGURED_ADMINS` is set, startup also syncs each user's `is_admin` flag from it), and the `workspace`, `workspace_repository`, and `workspace_slack_channel` tables that hold Open SWE's workspaces and the repository and Slack-channel bindings that route to them (a repository or Slack channel belongs to at most one workspace, enforced by the binding table's primary key, not just checked in application code), and the server refuses to start without it. Startup logs identify the setting and schema without printing the connection string. Once a startup's migrations have run, it copies any workspace records still sitting in the LangGraph Store — from before this table existed — into PostgreSQL and removes them from the Store; this import runs once and is a no-op on every later boot.

Analytics opens its own pooled SQLAlchemy/asyncpg connection to PostgreSQL. It does not provision or host a database. On LangGraph Platform, verify that the deployment exposes `POSTGRES_URI` to custom application code; having a working Agent Server Store does not by itself establish that access. For standalone deployments, explicitly supply `POSTGRES_URI` from the same database secret used for Agent Server's `DATABASE_URI`. Analytics reads only `POSTGRES_URI`, so setting `DATABASE_URI` alone does not enable it. PostgreSQL URLs using `postgres://` or `postgresql://` are accepted, and `sslmode` is translated for asyncpg. Preserve the deployment's TLS settings when mapping the connection.

After deployment, look for `Analytics database initialized` in startup logs. A signed-in administrator can then open `/dashboard/api/analytics/readiness` and check `configured: true` and `ready: true`; `/dashboard/api/analytics/outbox-status` reports pending and failed deliveries. These endpoints are added by the analytics API integration. Startup failures leave the app running, so a healthy homepage alone does not verify analytics. If readiness fails, check that the role can create the analytics schema and owns its tables for future migrations. No workspace UUID or collection timestamp needs to be supplied.

`collection_started_at` is set when the first event is successfully captured, not at startup. Reports read event-derived SQL tables and include `last_processed_at` plus indicators for queued or failed deliveries. Collection start and processing progress do not guarantee complete coverage: events can arrive late, and sources that have not been connected to analytics are absent. The Usage leaderboard, reviewer statistics, and PR outcomes all read these PostgreSQL projections. There is no analytics Store fallback or historical import. `reporting_cutover_at` is persisted once when the complete reporting integration first starts, and survives restarts. Reports exclude activity before this date; “All time” means since this cutover. The Usage page displays the date explicitly. Collection can begin earlier while the stack is rolling out, so `collection_started_at` and the reporting cutover describe different milestones.

## 2. LangSmith API key

Create a [LangSmith](https://smith.langchain.com/) API key under **Settings → API Keys** and save it as `LANGSMITH_API_KEY`. LangGraph Platform injects it into the deployment for you, along with `LANGSMITH_TRACING` and `LANGSMITH_PROJECT`; standalone deployments set it themselves.

The same key is used for tracing, sandboxes, and trace links. Trace links find your workspace through the key and the project by name, so no tenant or project ids are needed (`LANGSMITH_TENANT_ID` remains an override). Sandboxes boot from LangSmith's root snapshot, which ships `git`, `gh`, Python, `uv`, and Node, so there is nothing to configure; when your repositories need more, admins capture a sandbox image for a workspace from the **Workspaces** page later (see step 6). Other sandbox providers are covered in [CUSTOMIZATION.md](CUSTOMIZATION.md).

## 3. Create a GitHub App

Open SWE authenticates as a [GitHub App](https://docs.github.com/en/apps/creating-github-apps) to clone repositories, push branches, open pull requests, and sign users in to the dashboard.

Go to **GitHub Settings → Developer settings → [GitHub Apps](https://github.com/settings/apps) → New GitHub App** and fill in:

- **Callback URL**: `<URL>/dashboard/api/auth/callback`. GitHub Apps take several, one per line; for [local development](DEVELOPMENT.md) add `http://localhost:2024/dashboard/api/auth/callback`.
- **Request user authorization (OAuth) during installation**: off
- **Webhook URL**: `<URL>/webhooks/github`, **Webhook secret**: the output of `openssl rand -hex 32`, saved as `GITHUB_WEBHOOK_SECRET`
- **Repository permissions**:
  - Contents: Read & write
  - Pull requests: Read & write
  - Issues: Read & write
  - Checks: Read & write — reports an "Open SWE Review" check run on PRs while an auto-review runs and lets `/baby-sit` read third-party CI conclusions. Without it, check-run creation fails (logged, best-effort), reviews still work, and `/baby-sit` fails closed when it cannot read the complete check set.
  - Commit statuses: Read-only — required for `/baby-sit` to evaluate the complete PR status set, including integrations that report via legacy commit statuses.
  - Code scanning alerts: Read-only — optional; lets Open SWE inspect code-scanning alerts directly so it can identify and patch reported vulnerabilities. Source-code changes still use the Contents permission above.
  - Actions: Read-only — optional for CI diagnostics and log access. Grant **Read & write** only to enable `/baby-sit` to rerun evidence-backed flaky GitHub Actions jobs; existing installations must approve the elevation, and the token could then also cancel or delete runs.
  - Workflows: Read & write — lets Open SWE push branches containing explicitly requested GitHub Actions workflow changes.
  - Metadata: Read-only
- **Organization permissions**: Members: Read-only — verifies org membership for dashboard login and LangSmith trace-tool access when `ALLOWED_GITHUB_ORGS` is set. Without it that check fails closed.
- **Subscribe to events**: Issue comment, Pull request review, Pull request review comment, Check run, Check suite, Workflow run (the last three give `/baby-sit` immediate failure detection), and Status (optional; legacy commit-status integrations).

Click **Create GitHub App**, then collect from its settings page:

- **App ID** → `GITHUB_APP_ID`
- **Client ID** (starts with `Iv`) → `GITHUB_APP_CLIENT_ID`
- **Client secrets → Generate a new client secret** → `GITHUB_APP_CLIENT_SECRET`
- **Private keys → Generate a private key** downloads a `.pem`; its whole contents, BEGIN and END lines included → `GITHUB_APP_PRIVATE_KEY`

Finally **Install App** in the sidebar: pick the account and the repositories Open SWE may work in. The number at the end of the resulting URL, `https://github.com/settings/installations/<id>` (or `/organizations/<org>/settings/installations/<id>`), is `GITHUB_APP_INSTALLATION_ID`.

Give each deployment its own GitHub App, or at least a distinct mention handle (`OPEN_SWE_MENTION_TAGS`, see [Allowlists](#repository-allowlists-mention-handles-and-user-mapping)) when several share a GitHub organization.

## 4. Model providers and API keys

Open SWE calls models through [LangChain](https://python.langchain.com/) chat models named `provider:model`, so any provider you give a key for is available. Set at least one provider key unless you use an LLM gateway, such as the LangSmith Gateway described below:

| Provider | Variable | Notes |
|---|---|---|
| Anthropic | `ANTHROPIC_API_KEY` | Default model when it is the only key set |
| OpenAI | `OPENAI_API_KEY` | Default model otherwise. `OPENAI_BASE_URL` points at an OpenAI-compatible API |
| Google | `GOOGLE_API_KEY` | `google_genai:` models |
| Fireworks | `FIREWORKS_API_KEY` | `fireworks:` models |
| Groq | `GROQ_API_KEY` | `groq:` models |
| Baseten | `BASETEN_API_KEY` | `baseten:` models |

**LangSmith LLM Gateway.** Instead of per-provider keys, route every model call through the gateway with one LangSmith key that has the `gateway:invoke` permission, set as `LANGSMITH_GATEWAY_API_KEY`. Setting that key turns the gateway on; `LANGSMITH_GATEWAY_ENABLED=true|false` forces it either way (with `true` and no gateway key, `LANGSMITH_API_KEY` is used, which on LangGraph Platform may lack the permission). `LANGSMITH_GATEWAY_BASE_URL` points at a regional or self-hosted gateway. Admins can also toggle the gateway per team in the dashboard.

**Which model runs.** The deployment default comes from the supported-model list in `agent/dashboard/options.py` (an Anthropic model when only an Anthropic key is configured, otherwise an OpenAI one); override it with `LLM_MODEL_ID` (`provider:model`) and `LLM_REASONING_EFFORT` (`low`, `medium`, `high`, `max`), and name a `LLM_FALLBACK_MODEL_ID` for when the primary provider fails. Admins set the instance default, which each workspace can override, under **Admin → Global defaults**, and each user can pick their own model and effort under **My settings**. Model ids and their providers are described in [CUSTOMIZATION.md](CUSTOMIZATION.md).

**Other API keys.** `EXA_API_KEY` (from [dashboard.exa.ai](https://dashboard.exa.ai)) enables the web search tool. `REVIEWER_OUTCOMES_DATASET` names the LangSmith dataset the reviewer records finding outcomes in (default `openswe-reviewer-outcomes`).

## 5. Create the Slack app

Open SWE answers `@`-mentions in Slack and posts its progress there, and Slack is how most teams start runs. The app posts events to your deployment's URL, so it needs the same public URL as the GitHub App.

1. Go to [api.slack.com/apps](https://api.slack.com/apps) → **Create New App** → **From a manifest**, and paste the manifest below with `<your-url>` replaced by the hostname of `<URL>`, for example `my-open-swe-abc123.us.langgraph.app`; the manifest already supplies the `https://`.

<details>
<summary>Slack App Manifest</summary>

```json
{
    "display_information": {
        "name": "Open SWE",
        "description": "Enables Open SWE to interact with your workspace",
        "background_color": "#000000"
    },
    "features": {
        "app_home": {
            "home_tab_enabled": false,
            "messages_tab_enabled": true,
            "messages_tab_read_only_enabled": false
        },
        "bot_user": {
            "display_name": "Open SWE",
            "always_online": true
        },
        "slash_commands": [
            {
                "command": "/oswe",
                "url": "https://<your-url>/webhooks/slack/commands",
                "description": "Ask Open SWE",
                "usage_hint": "[your request or question]",
                "should_escape": false
            }
        ]
    },
    "oauth_config": {
        "redirect_urls": [
            "https://<your-url>/dashboard/api/slack/callback"
        ],
        "scopes": {
            "bot": [
                "reactions:write",
                "commands",
                "app_mentions:read",
                "channels:history",
                "channels:read",
                "channels:join",
                "chat:write",
                "files:read",
                "files:write",
                "groups:history",
                "groups:read",
                "im:history",
                "im:read",
                "im:write",
                "mpim:history",
                "mpim:read",
                "team:read",
                "users:read",
                "users:read.email"
            ]
        }
    },
    "settings": {
        "event_subscriptions": {
            "request_url": "https://<your-url>/webhooks/slack",
            "bot_events": [
                "app_mention",
                "channel_created",
                "channel_rename",
                "channel_archive",
                "message.channels",
                "message.im",
                "message.mpim"
            ]
        },
        "interactivity": {
            "is_enabled": true,
            "request_url": "https://<your-url>/webhooks/slack/interactivity"
        },
        "org_deploy_enabled": false,
        "socket_mode_enabled": false,
        "token_rotation_enabled": false
    }
}
```

</details>

2. Install the app to your workspace.
3. Add to the environment (step 6):

```bash
SLACK_BOT_TOKEN=""        # OAuth & Permissions → Bot User OAuth Token (xoxb-...)
SLACK_SIGNING_SECRET=""   # Basic Information → App Credentials → Signing Secret
SLACK_APP_ID=""           # Basic Information → App ID (A...); Incidents accepts events only from this app
SLACK_BOT_USER_ID=""      # the bot's member id (open the bot's profile in Slack → ⋮ → Copy member ID)
SLACK_BOT_USERNAME=""     # the bot's handle, e.g. open-swe
```

`/oswe <request or question>` answers or carries out a request without starting a Slack thread: replies are ephemeral, visible only to whoever asked, and the immediate acknowledgement links to the thread in the web dashboard. Each person's commands in a channel share one private scratch thread, kept out of everyone's thread list; continuing it on the web makes it an ordinary thread. Substantial work belongs in a thread of its own, which Open SWE starts in the channel.

Both Slack URLs must point at the Open SWE deployment, and Block Kit buttons only work with Interactivity enabled and pointed at `/webhooks/slack/interactivity`. Slack messages are routed to the thread's repository, a `repo:owner/name` token in the message, or the team default repository. Open SWE refuses Slack Connect channels (`is_ext_shared`) and fails closed when it cannot verify a channel.

`files:read` lets Open SWE download non-image files attached to a message (archives, logs, CSVs) and stage them in the thread's sandbox, where the agent reads them by path. Existing installations must add the scope in **OAuth & Permissions** and reinstall the app before attachments reach the agent; without it, uploads stay invisible and only the message text is used.

Slack verifies the events Request URL the first time it can reach it; if the backend is not up yet when you create the app, use **Retry** under **Event Subscriptions** after step 7.

## 6. Set the environment variables

```bash
LANGSMITH_HOST_API_URL="<URL>"        # Platform injects this; set it for standalone Docker
LANGSMITH_API_KEY=""                  # step 2; injected by LangGraph Platform
LANGSMITH_TRACING="true"              # injected by LangGraph Platform
ANTHROPIC_API_KEY=""                  # step 4: any provider key, or LANGSMITH_GATEWAY_API_KEY

GITHUB_APP_ID=""                      # step 3
GITHUB_APP_CLIENT_ID=""
GITHUB_APP_CLIENT_SECRET=""
GITHUB_APP_PRIVATE_KEY="-----BEGIN RSA PRIVATE KEY-----\n...\n-----END RSA PRIVATE KEY-----"   # one line with \n between the PEM lines, or the multi-line value your platform accepts
GITHUB_WEBHOOK_SECRET=""
GITHUB_APP_INSTALLATION_ID=""
ALLOWED_GITHUB_ORGS=""               # required unless ALLOWED_GITHUB_USERS is set
ALLOWED_GITHUB_USERS=""              # required unless ALLOWED_GITHUB_ORGS is set

SLACK_BOT_TOKEN=""                    # step 5
SLACK_SIGNING_SECRET=""
SLACK_BOT_USER_ID=""
SLACK_BOT_USERNAME=""

TOKEN_ENCRYPTION_KEY=""               # openssl rand -base64 32  (encrypts stored GitHub and Slack tokens)
DASHBOARD_JWT_SECRET=""               # openssl rand -hex 32     (signs the session cookie and OAuth state)
CONFIGURED_ADMINS=""                  # GitHub logins or emails, comma-separated; admins see the Admin pages
```

On LangGraph Platform, set them under the deployment's environment variables; saving rolls out a new revision. The platform injects the URL for each deployment and preview through `LANGSMITH_HOST_API_URL`. With Docker, put the variables in the file you pass as `--env-file` and set `LANGSMITH_HOST_API_URL` to the public ingress URL. `DASHBOARD_BASE_URL` and `DASHBOARD_API_BASE_URL` are not needed because the dashboard is served from the same origin.

## 7. Verify it works

**Dashboard.** Open `<URL>`, click **Sign in with GitHub**, and you should land logged in. With your login in `CONFIGURED_ADMINS`, the **Admin** pages (Model defaults, Users, Sandbox, …) appear, along with the **Workspaces** page at `/workspaces` that every signed-in user can see. Set **Admin → Global defaults → Default Repository** so runs that name no repository have somewhere to go. Start a task from the composer. Every run gets a sandbox booted from LangSmith's root snapshot; when your repositories need extra toolchains preinstalled, an admin can start an **admin thread** (the Admin toggle in the composer), have the agent set the sandbox up, and capture it from the **Workspaces** page into the `default` workspace, which later runs boot from.

**Slack.** Invite the bot to a channel and mention it: `@Open SWE what's in the repo?`. It replies in a thread. Public runs use the workspace GitHub App for agent operations, and user-owned PRs are opened as the thread's initiating GitHub user. Link the Slack user to a GitHub login before starting the thread, either by signing in to the dashboard once or through [Sign in with Slack](#slack-sign-in-and-code-channels).

**GitHub.** GitHub-triggered conversations are public. Agent GitHub operations use the App installation identity, while PRs use the initiating commenter's OAuth. The commenter must have a linked account; an unmapped commenter is skipped with a warning in the server log. Comment `@openswe what files are in this repo?` on an issue in a repository where the App is installed. Within a few seconds you should see a 👀 reaction, a run in your LangSmith project, and a reply comment. GitHub lists every delivery and its response under the App's **Advanced** tab.

**How a run picks its workspace.** A workspace owns repositories, Slack channels, MCP connections, and workspace settings, and carries the sandbox prompt/snapshot/scripts described above. New work is routed to a workspace in this order, first match wins: the thread it belongs to already has one; the message that opened the thread carries a `workspace:<slug>` tag (`env:<slug>` still works as an alias); the repository belongs to a workspace; the Slack channel it was posted in is bound to a workspace; the user has a default workspace set under **My settings**; otherwise it falls back to `default`. GitHub events for a repository that no workspace owns follow `OPEN_SWE_UNASSIGNED_REPO_WORKSPACE`: `default` (the default) routes them to the `default` workspace, and `ignore` drops them without creating a run. Workspace records and their repository and Slack-channel bindings live in PostgreSQL (see **Analytics storage** above), not the LangGraph Store; a workspace's MCP connections and workspace settings do stay in the Store, keyed by the workspace's slug.

---

## Optional add-ons

Open a section when you want that feature; everything above keeps working without it.

<details id="slack-sign-in-and-code-channels">
<summary><strong>Slack: "Sign in with Slack" linking and code channels</strong></summary>

**"Sign in with Slack" account linking.** Lets a user link their Slack identity to their GitHub login from **My settings**, so Slack-triggered runs resolve to the right GitHub user through Slack's verified claims. Without it, Slack senders stay unlinked until they connect from **My settings**; **Admin → Users** shows who has. The manifest already registers the OIDC redirect; make sure the `openid`, `email`, and `profile` user scopes are available, then set `SLACK_CLIENT_ID` and `SLACK_CLIENT_SECRET` from **Basic Information → App Credentials**, and optionally `SLACK_TEAM_ID` (`T...`) to restrict linking to one workspace. When they are unset the link is simply hidden.

**Code channels (early access).** To enable Slack [code channels](https://api.slack.com/partners/code-channels), open **Admin → Slack integration**, turn on **Slack Code Channels**, copy the generated manifest, update the Slack app, and reinstall it. In a code channel the whole channel is one Open SWE session: it answers without an `@`-mention, replies at the channel level by default, reports session status, and keeps the context bar current; the `manage_code_channel` tool covers channel lifecycle, status, views, and canvases. This requires the `code_channels:manage` bot scope, the `agent_session_stopped` and `code_channel_action` bot events, and `features.code_channels.enabled`; `slash_command_url` delivers runtime-registered commands to the signed Open SWE endpoint. If your workspace is not enrolled, leave the toggle off.

</details>

<details id="incidents">
<summary><strong>Incidents</strong></summary>

The **Incidents** dashboard at `/incidents` investigates public internal Slack channels. Each incident is one persistent, system-owned conversation on the main `agent` graph, driven by the same Slack webhook path as code channels, with the normal sandbox, coding/PR tools, subagents, organization skills, and configured workspace integrations. Responders can copy the agent's postmortem summary and consult retained incident history.

1. Install or reinstall the Slack manifest above and set `SLACK_APP_ID` from **Basic Information → App ID**. Incidents needs the `channel_created`, `channel_rename`, `channel_archive`, `message.channels`, and `app_mention` events. The manifest includes the public-channel scopes and `users:read` / `users:read.email` needed for authorized Slack controls.
2. Access follows the rest of Open SWE: any signed-in dashboard user can read incidents, postmortems, and history, and only `CONFIGURED_ADMINS` can change incident settings. In Slack, anyone in an incident channel can pause, resume, or complete it; asking the agent a question or starting an incident manually requires a connected Open SWE account (Sign in with Slack in the dashboard), the same as mentioning Open SWE anywhere else. Reads recheck current channel access.
3. Optionally give the agent an incident tracker: add its MCP server, for example incident.io at `https://mcp.incident.io/mcp`, under **Workspaces → the workspace → MCP connections** following [Workspace MCP servers](CUSTOMIZATION.md#workspace-mcp-servers). The agent uses those tools like any other workspace integration, only for an explicit responder request.
4. In **Admin → Incidents**, set a channel prefix such as `inc-` and optionally a model and a model-call limit per turn. Enable Incidents, then create a matching public channel or rename one into the prefix. Anyone with a connected Open SWE account can also mention the bot in any public channel and ask it to monitor that channel as an incident; the agent's `manage_incident` tool enrolls it. To turn it off, anyone in the channel mentions the bot with `pause` (stops automatic analysis) or `complete` (ends the incident), asks it in plain words, or uses the buttons on the incident's dashboard page.

Slack findings and control notices use compact messages directly in the incident channel. Questions posted in the channel receive channel replies; questions inside an existing thread receive replies in that thread. Detailed hypotheses, questions, coverage gaps, and citations remain in the incident report and postmortem. Changes only to those detailed hypotheses or questions do not generate another Slack update.

Ordinary channel messages, including bot alerts, are queued as context and analyzed together in one turn about 15 seconds after the first arrives; enrollment queues the most recent channel history the same way. Direct questions and pause/stop controls bypass the delay. As in the main Slack handler, a direct mention interrupts an active turn, while ordinary messages wait for the next one. Nothing runs while the channel is quiet, so there is no idle timeout or watch limit; complete, pause, or archiving the channel stops the bot.

The agent stores its latest postmortem summary as Markdown in the existing LangGraph Store. The **Postmortem** tab renders it and **Copy incident** copies the text and source links for use elsewhere. **History** searches retained incident metadata and summaries, including incidents whose raw context has expired. There is no document editor or revision-history UI.

Public status-page publishing is not implemented. Authorized responders can ask the agent to edit code, open a PR as the GitHub App, or use configured integrations for a specified action. Automatic turns investigate and propose mitigation; alerts and ordinary channel messages do not authorize external changes. Personal integrations remain unavailable in these system-owned threads.

</details>

<details id="linear">
<summary><strong>Linear</strong></summary>

Open SWE listens for Linear comments that mention `@openswe`.

1. **Settings → API → Webhooks → New webhook**: label `Open SWE`, URL `<URL>/webhooks/linear`, a secret from `openssl rand -hex 32` saved as `LINEAR_WEBHOOK_SECRET`, and under **Data change events** only **Comments → Create**.
2. Add a Linear MCP server named `linear` under **Workspaces → the workspace → MCP connections** and select the tools Open SWE may use. Include `save_comment` (or `create_comment` if offered) so the backend can post run, authentication, and sandbox failure notices even after the agent stops.
3. Set a workspace default repository under **Open SWE Agent**. Add a `repo:owner/name` token or GitHub URL to a Linear comment when the issue belongs to another repository.

**Verify:** comment `@openswe what files are in this repo?` on an issue, adding `repo:owner/name` when needed.

</details>

<details id="dashboard-on-its-own-origin">
<summary><strong>Dashboard on its own origin (separate frontend deployment)</strong></summary>

The bundled dashboard needs none of this. Read on only if the dashboard is deployed separately from the backend.

**A separate frontend deployment.** The `ui/` app also builds to a Nitro server (`ui/Dockerfile`) that renders on request. Set its `DASHBOARD_API_URL` to the backend URL; browser requests to `/dashboard/api/*` and webhook deliveries to `/webhooks/*` are proxied there, and server renders forward the `osw_session` cookie. Set `DASHBOARD_BASE_URL` and `DASHBOARD_API_BASE_URL` on the backend to the frontend origin and register `<frontend origin>/dashboard/api/auth/callback` on the GitHub App. To have the browser call the backend cross-origin instead, build the UI with `VITE_DASHBOARD_API_BASE_URL` set to the backend origin, keep `DASHBOARD_API_BASE_URL` on the backend origin, and add the frontend origin to `DASHBOARD_ALLOWED_ORIGINS`; the session is then resolved on the client after hydration.

**Mount prefix.** If the server runs under a LangGraph `http.mount_prefix`, the Platform image builds the UI for that prefix automatically; locally pass it to the build (`DASHBOARD_BASE_PATH=/<prefix>/ make build-dashboard`) and keep `LANGSMITH_HOST_API_URL` on the mounted URL.

**Datadog RUM.** Set `VITE_DATADOG_APPLICATION_ID` and `VITE_DATADOG_CLIENT_TOKEN` when building. Optional: `VITE_DATADOG_SITE` (default `us5.datadoghq.com`), `VITE_DATADOG_SERVICE` (default `open-swe-dashboard`), `VITE_DATADOG_ENV`, `VITE_DATADOG_VERSION`, `VITE_DATADOG_SESSION_SAMPLE_RATE` and `VITE_DATADOG_SESSION_REPLAY_SAMPLE_RATE` (default `100`). Session Replay masks all content and telemetry strips query strings and fragments. `VITE_` values are public in the bundle; use a client token, never an API or application key. The dashboard also reports two custom duration vitals, `thread_load` and `agent_run`, with their phase breakdown in the vital context (RUM Explorer: `@type:vital @vital.name:thread_load`); see [docs/DEVELOPMENT.md](DEVELOPMENT.md#profiling-thread-load-and-streaming) for what they measure.

</details>

<details id="repository-allowlists-mention-handles-and-user-mapping">
<summary><strong>Repository allowlists, mention handles, and users</strong></summary>

**Mention handles.** The handles this deployment answers to default to `@openswe,@open-swe,@openswe-dev`; set `OPEN_SWE_MENTION_TAGS` to change them. Handles match on a word boundary, so `@openswe` does not fire on `@openswe-staging`. Set `EXTRA_INTERNAL_BOT_LOGINS` (e.g. `openswe-staging[bot]`) to treat other Open SWE deployments' comments as internal rather than untrusted.

**Allowlists.**

```bash
ALLOWED_GITHUB_ORGS="langchain-ai,anthropics"                        # org members allowed to log in; all repos in these orgs
ALLOWED_GITHUB_USERS="octocat,hubot"                                 # individual users allowed to log in
ALLOWED_GITHUB_REPOS="some-user/their-repo,another-org/specific-repo"  # specific owner/repo pairs
PUBLIC_REPO_ORG_GATE=""   # single org whose members may trigger runs on *public* repos; empty = no gate
OPEN_SWE_UNASSIGNED_REPO_WORKSPACE="default"   # GitHub events for a repo no workspace owns: "default" (the default) routes to the default workspace, "ignore" drops them
```

Shared backend startup requires at least one entry in `ALLOWED_GITHUB_ORGS` or `ALLOWED_GITHUB_USERS`; an empty value in both stops the server. The desktop app's authenticated private local backend is exempt because it supports local mode without GitHub. When both are configured, they form a union: dashboard login accepts an explicitly listed user **or** an active member of a listed organization. Organization membership is verified server-side with the installation token and fails closed on any API error; install the App in every listed organization and grant **Organization → Members: Read-only**. A GitHub or Linear webhook is accepted if the repo's org is in `ALLOWED_GITHUB_ORGS` **or** the `owner/repo` is in `ALLOWED_GITHUB_REPOS`; both repository allowlists empty allows every installed repository. For Slack and dashboard requests, `ALLOWED_GITHUB_ORGS` also adds a prompt-level guard: editing a repository outside those orgs requires the user to name it with its full `https://github.com/<owner>/<repo>` URL. When team LangSmith credentials are connected, every active member of a listed organization can use the read-only LangSmith trace tools, so only list organizations whose full membership may see team-level trace data.

**Users.** A person gets a `users` row on their first dashboard sign-in, with their GitHub account as its first identity; connecting Slack from **My settings** adds the Slack account to the same row, which is how a Slack sender resolves to a GitHub login. **Admin → Users** lists everyone Open SWE knows. An unlinked person who tags Open SWE in Slack gets a run with the GitHub App's installation permissions and a "link your GitHub account" prompt; signing in and connecting Slack completes it. Records from the older Store-backed user mapping are imported into `users` on the first startup that finds them, then deleted.

**Default repository.** Runs that name no repository use the workspace's default repository (**Workspaces → the workspace → Default repository**, inheriting **Admin → Default repository** unless overridden), seeded from `DEFAULT_REPO_OWNER` / `DEFAULT_REPO_NAME` when set; `SLACK_REPO_OWNER` / `SLACK_REPO_NAME` are a Slack-only fallback.

</details>

<details id="rotating-token_encryption_key">
<summary><strong>Rotating <code>TOKEN_ENCRYPTION_KEY</code></strong></summary>

`TOKEN_ENCRYPTION_KEY` accepts a single Fernet key or a comma- or newline-separated **ordered list, most recent first**. Writes use the first key; reads try every key in order.

1. Generate a new key: `openssl rand -base64 32`.
2. Prepend it, keeping the old key second: `TOKEN_ENCRYPTION_KEY="<new_key>,<old_key>"`, and restart.
3. Once every active user has signed in again (each fresh OAuth flow re-encrypts under the new key), drop the old key. Anything still encrypted under it fails to decrypt and that user is asked to sign in again.

</details>

## Troubleshooting

### Webhook not receiving events

- The URL configured in GitHub, Slack, or Linear must be the deployment's URL; GitHub shows each delivery and its response under the App's **Advanced** tab. A new webhook or signing secret takes effect only after the deployment restarts with it; deliveries in between are rejected as `Invalid signature`, and Slack then needs **Retry** on its Request URL under **Event Subscriptions**.
- Enable the right events: Issue comment and the pull request review events for GitHub, `app_mention` for Slack, Comments → Create for Linear.
- Webhook secrets are required: without `GITHUB_WEBHOOK_SECRET`, `SLACK_SIGNING_SECRET`, or `LINEAR_WEBHOOK_SECRET`, every request to that endpoint is rejected with 401.

### Thread credential scope

Public threads use the GitHub App installation identity for agent GitHub
operations. PRs from user-owned threads are opened with the authenticated current
run requester's stored GitHub OAuth token. For example, if Alice starts a Slack
task and Bob triggers a follow-up run asking to create the PR, the new PR is
opened as Bob. Alice remains the task owner, and existing PR authors are unchanged.
If the requester's identity or token is unavailable, PR creation fails without
falling back to the task owner or bot. The requester must have access to the target
repository. Public PR creation first verifies that the target repository is
accessible through the configured workspace installation. System-owned threads, including scheduled automations, open PRs
as the GitHub App. Existing threads without recorded ownership retain bot PR
authorship. Scheduled runs check repository access with the workspace GitHub App.
They record the automation creator for auditing but do not require that person's
OAuth token or inject their GitHub login or email as the agent's execution identity.
Admin schedules retain their management tools through authorization tied to the
scheduled invocation. The graph and tools recheck the creator's current admin
status; later participants do not inherit that authorization. Automation management
from these system runs also uses workspace credentials.

Authorship is bound to the publishing run, not inferred from conversation text.
Slack follow-ups carry their own requester identity whether they interrupt or
queue behind an active run. Dashboard messages and Slack edits injected into an
existing run do not change its identity. A collaborator must start a new run to
publish under their own account. Workflow push approval starts a run with the
authenticated actor's identity. Background-task
completion runs cannot reliably identify the launching requester, so PR creation
from user-owned threads is blocked in those runs. Start a direct user-triggered
run to publish. System-owned and legacy unowned threads retain bot authorship.
This prevents the publisher from approving their
own PR; it does not prevent other task participants from approving it.

Public threads load workspace MCP connections and organization skills. Personal
Notion connections, user skills, and user custom instructions are available only in a private thread
started by its immutable owner. The same ownership check applies when a personal
MCP tool refreshes its credentials at execution time.

Private threads use their owner's stored GitHub OAuth token for server-side
GitHub operations and PR creation. If that token is unavailable, the owner must
sign in again; the run does not fall back to the bot. Sandbox GitHub proxy access
continues to use the GitHub App installation token in both kinds of thread.
User identity and membership checks still apply to public runs.

### GitHub authentication errors

- Check `GITHUB_APP_ID`, `GITHUB_APP_PRIVATE_KEY`, and `GITHUB_APP_INSTALLATION_ID`. The private key must include the full `-----BEGIN RSA PRIVATE KEY-----` and `-----END RSA PRIVATE KEY-----` lines; in a `.env` file write it as one double-quoted line with `\n` between the PEM lines.
- Make sure the App is installed on the target repositories.

### Dashboard login fails or won't stay logged in

- `500 GITHUB_APP_CLIENT_ID not configured` (or client secret): set `GITHUB_APP_CLIENT_ID`, `GITHUB_APP_CLIENT_SECRET`, and `DASHBOARD_JWT_SECRET`.
- `redirect_uri is not associated with this application`: the App must list `<URL you opened the dashboard on>/dashboard/api/auth/callback`. Add it in the App's settings.
- Login redirects but the session does not stick: use `https://` and open the dashboard on the deployment's own URL.
- `403 CSRF check failed` on saves: the request's `Origin` is neither `DASHBOARD_BASE_URL` (defaults to `LANGSMITH_HOST_API_URL`) nor in `DASHBOARD_ALLOWED_ORIGINS`.
- Startup fails with `ALLOWED_GITHUB_ORGS or ALLOWED_GITHUB_USERS must be configured`: set at least one nonempty login allowlist.
- Login rejected with an authorization error: add the login to `ALLOWED_GITHUB_USERS`, or configure `ALLOWED_GITHUB_ORGS` and grant the App Organization → Members permission.
- Admin pages 403: add your GitHub login or email to `CONFIGURED_ADMINS`.

### Dashboard shows the LangGraph JSON instead of the UI, or 404s at `/`

- The image has no dashboard build. On LangGraph Platform, check the build log for `dashboard build failed`; with Docker, run `make build-dashboard` before `docker build`, or set `DASHBOARD_STATIC_DIR` to a directory holding a build.

### Sandbox creation failures

- `LANGSMITH_API_KEY` must be set and valid, and the workspace must have sandbox access (403 on the sandbox endpoints means it does not; contact LangSmith support).
- Check LangSmith sandbox quotas in your workspace settings.
- `Failed to create sandbox from snapshot '<id>'` means a workspace's captured snapshot or the base snapshot no longer exists or is not `ready`; delete or recapture it from the **Workspaces** page (or clear **Admin → Sandbox → Base snapshot**) to fall back to the root snapshot.

### Agent not responding to comments

- GitHub: the comment must contain a configured handle (`@openswe` by default, case-insensitive), and the commenter must have signed in to the dashboard once; otherwise the log says `No email mapping for GitHub user`.
- Linear: the comment must contain the handle; Slack: the bot must be in the channel and `@`-mentioned.
- Check the server log for webhook processing errors.

### Token encryption errors

- `TOKEN_ENCRYPTION_KEY` must be set to a valid Fernet key (`openssl rand -base64 32`), or an ordered list of them; see [Rotating `TOKEN_ENCRYPTION_KEY`](#rotating-token_encryption_key).
