# Installation Guide

This guide deploys Robin for a team.
To run it on your own machine while developing, use the [development guide](DEVELOPMENT.md) instead.
For an existing deployment, complete the [Robin rollout checklist](ROBIN_ROLLOUT.md), including external app profiles and required-check rules.

Robin is one deployment: a LangGraph server that runs the graphs declared in [`langgraph.json`](../langgraph.json), the FastAPI app (`openswe.webapp:app`) that owns the webhooks and the dashboard API, and the web dashboard, served from the same origin at `/`.
Webhooks, the dashboard, GitHub login, and the API all share the deployment's URL, so there is no second frontend deploy and no cross-origin cookie or CORS setup.

## Desktop client

Robin Desktop connects to an existing deployment; it does not host the backend.
Install `Robin.app` from a `Robin-*` release download in the [releases](https://github.com/langchain-ai/open-swe/releases), or build and install from source with `make install-desktop`.
Release downloads retain legacy `Open-SWE-*` aliases for existing installers.
The app bundles the `robin` CLI, with `oswe` retained as a compatibility alias; existing profiles, credentials, and the `com.langchain.openswe` bundle ID stay unchanged.

The Homebrew cask identifier remains `langchain-ai/tap/open-swe-desktop` until the external tap is updated:

```sh
brew install --cask langchain-ai/tap/open-swe-desktop
```

The tap needs its app path and CLI links updated before it can install Robin-branded releases; see the rollout checklist.
For current platform requirements and installation details, see [Robin Desktop](../desktop/README.md#install-on-macos).

## Repository trust model

**Robin is intended for a team trusted with shared repository access, not for users who require repository-level isolation within one deployment.**
Member-started coding sandboxes normally use the GitHub App's installation-wide credentials, not the initiating user's GitHub permissions.
A user can therefore have the agent read or write an installed repository they cannot access personally, subject to the App's permissions and GitHub protections.

Before admitting users, limit the App installation and sandbox images to repositories and source that every admitted user is trusted to access.
Repository selection, integration bindings, and private-thread visibility do not narrow sandbox GitHub access.
Source preloaded into an image remains readable even if a later token is narrowed.
Separate trust groups need separate deployments, App installations, and appropriately restricted images.

Trusted users can still expose the agent to malicious instructions in repository content or other external input.
Prompt instructions are not a repository authorization boundary.
User-authorized PR creation does not constrain every shell or GitHub API operation in the sandbox.

Some workflows, including reviewers and public-repository event threads, use narrower tokens; see [GitHub access and the sandbox image](reference/execution.md#github-access-and-the-sandbox-image).
For the distinction between server-side user credentials and sandbox credentials, see [Thread credential scope](#thread-credential-scope).

Robin's GitHub approvals can affect branch protection.
See the [stale-approval threat model](reference/review-approval-threat-model.md) for withdrawal behavior, deployment scoping, and best-effort cleanup limitations.

## Prerequisites

What a deployment needs:

| Value | How you get it |
|---|---|
| `LANGSMITH_API_KEY` | LangSmith → Settings → API Keys. LangGraph Platform injects it. |
| `LANGSMITH_GATEWAY_API_KEY` for the LangSmith LLM Gateway | A LangSmith key with `gateway:invoke` (step 4) |
| `GITHUB_APP_ID`, `GITHUB_APP_CLIENT_ID`, `GITHUB_APP_CLIENT_SECRET`, `GITHUB_APP_PRIVATE_KEY`, `GITHUB_APP_INSTALLATION_ID` | The GitHub App you create in step 3 |
| `SLACK_BOT_TOKEN`, `SLACK_SIGNING_SECRET`, `SLACK_APP_ID`, `SLACK_BOT_USER_ID`, `SLACK_BOT_USERNAME` | The Slack app you create in step 5 |
| `TOKEN_ENCRYPTION_KEY`, `DASHBOARD_JWT_SECRET` | Two random secrets you generate (step 6) |
| `ALLOWED_GITHUB_ORGS` or `ALLOWED_GITHUB_USERS` | The GitHub organizations or users allowed to log in (step 6) |
| `CONFIGURED_ADMINS` | The GitHub logins or emails of your admins (step 6) |
| `LANGGRAPH_URL` | The deployment's own public URL |

GitHub and Slack are the two surfaces every deployment has; Linear is an optional add-on.
The backend refuses to start if any of the five required Slack app variables above are missing or blank.
Every variable Robin reads is declared in `openswe/config.py` with its description and default; that file is the complete reference.

### Optional PostHog usage tracking

Set `POSTHOG_API_KEY` to your PostHog project API key.
`POSTHOG_HOST` defaults to `https://us.i.posthog.com`; use `https://eu.i.posthog.com` for EU projects or your self-hosted ingestion host.
Without a key, capture is disabled.
The dashboard reads the same key from `/dashboard/api/analytics/config` and captures in the browser with posthog-js, so no frontend build setting is needed.

The dashboard sends `$pageview` and `$pageleave` events grouped into PostHog sessions, with the page title, a normalized `page_name` and URLs whose ids and query strings are replaced by placeholders (`/agents/:threadId`); autocapture, heatmaps, session recording, exception capture and web vitals are off.
`platform` is `web` or `desktop` (with `desktop_version`), so desktop app usage can be told apart.
MCP executions send `MCP Tool Called` with tool name, `is_error`, and the run repository name; persisted webhook deliveries send `Webhook Received` with source, event type, action, resolved repository/PR IDs, and the repository name on GitHub events.
`DD_ENV` identifies the deployment.
Resolved users share the internal user UUID, with email and GitHub login traits on usage events.
Signed-out dashboard visits create no person profiles; unresolved MCP usage is skipped.
A webhook whose sender is no Robin user counts as that account (`github:<id>`, `slack:<id>`); bots get a person profile marked `is_bot`, people without an account get none, and events with no sender use `open-swe:webhook:<source>`.
Webhooks on a pull request also carry `pull_request_author_id`, the author's user UUID, so bot activity on a PR can be credited to its author.
Private events, such as a person's own threads, record only who acted, the source and event type, marked `visibility: private`; PRs they open are public and tracked in full.

Delivery is best effort, not a durable export or historical backfill.
Tool arguments/results, page URLs/query strings/thread identifiers, and raw webhook payloads are excluded; only allowlisted metadata properties are exported; IP geolocation is disabled.

### Optional Segment usage tracking

Set the existing `SEGMENT_BROWSER_WRITE_KEY` on the connected backend to enable both web/desktop UI capture and queued backend lifecycle/runtime activity delivery into the same Segment source.
This public source write key is returned by `/dashboard/api/analytics/config`; no additional key or frontend build setting is needed.
Without it, neither path captures Segment events and the UI SDK is not loaded; there is no bundled fallback key.
Deployments that already configure this key begin exporting backend lifecycle and runtime activity when this update deploys, without a separate backend opt-in.
The shared public write key does not authenticate event origin: clients can submit arbitrary event names, user IDs and properties, including records labeled as backend activity.
Treat Segment data as directional product analytics, not authoritative billing, audit, authorization or outcome records; use the PostgreSQL analytics behind `/usage` for authoritative captured lifecycle data.

Use dedicated Robin sources and separate production from non-production deployments.
Configure cloud-mode destinations in Segment; no destination SDKs, remote plugins or Signals are loaded in the UI.
For warehouse separation, map these sources to their own dataset rather than sharing another app's event tables.
A backend and downloaded UI deployment enables this coverage; no native desktop shell release is required.
Existing PostHog capture and PostgreSQL analytics, including `/usage`, keep their existing behavior and identity semantics.

#### Browser intent and feature use

The UI sends `page` events on initial load and pathname navigation, named by the matched route template (dynamic parameters become `:id`).
Query-only changes do not count as page views.
`agents_thread_created` still means an accepted new thread, with `run_target` (`local` or `cloud`) and `has_attachments`.
`agents_message_submitted` still means a follow-up attempt, with `mode` (`new_turn`, `queue` or `steer`) and `has_attachments`; it excludes offloading.

`agents_action_started` and `agents_action_completed` add bounded `action`, `result` (`accepted` or `failed`) and `failure_kind` properties for thread starts, follow-ups, queue/steer/offload/cancel, project membership and filing, thread sharing, integration connection, automations, explicit preference saves and review request/submission/rerun.
Acceptance is the API's acceptance, not successful agent completion; a later stream failure does not rewrite an accepted action.
OAuth redirects only record the start when the browser cannot observe the outcome.
`agents_feature_opened` records intentional panel/tab opens for terminal, browser, files, diff, artifacts, background work, apps and review surfaces, not automatic panel opens or background reads.
Autosaved instructions are not counted as explicit settings saves.

Browser events carry `product: open-swe`, `environment` from `DD_ENV`, `platform` (`web` or `desktop`), optional `desktop_version`, and `schema_version: 1`.
Users identify by canonical Robin `users.id`, with email and GitHub login only as identify traits.
Actions retain their starting actor across asynchronous work; sign-out or account changes discard pending events and prevent results being attributed to the next account.
Identity persists in localStorage (with an in-memory fallback), including on the desktop custom scheme.
Early events queue in memory up to 100 entries with their original timestamp and route.
Browser delivery is best effort and may be blocked or lost.
The backend emits lifecycle events for observed run outcomes, but records in the shared Segment source cannot prove backend origin.

#### Backend activity and outcomes

`agents_request_started` distinguishes `human_request`, `automatic_work` and `unknown` using stamped message provenance, not message roles or content.
`agents_tool_executed` records reported tool results as `agent_work`, with a finite built-in tool name (otherwise `external` or `unknown`) and `is_error`; it does not imply human intent or include arguments/results.
These events use report receipt time unless a request's signed current invocation supplies its start time.
Carried reports never borrow a later invocation's owner or start time.

Existing lifecycle events are exported as `agents_<event_name>` with dots replaced by underscores:

- `agents_run_started`, `agents_run_completed`, `agents_run_failed`, `agents_run_canceled`, and `agents_run_cost_recorded`, including available token counts, bounded failure/cancellation classifications and configured/effective model identifiers.
- PR opening, observation, run linking, merge, close, reopen and distance measurements; review publication and finding observations.
- Task completion, acceptance and rework requests, and submitted user feedback.

Lifecycle events retain their original event ID and occurrence time.
Cost events are observations: select the highest `observation_revision` per `run_id`, rather than summing revisions or adding terminal token counts to cost-event token counts.
Unavailable/partial observations are not zero-cost completed runs.
Opaque `thread_id`, lifecycle `run_id` and `runtime_run_id` support joins between request, tool and lifecycle data; a runtime run ID is not an invocation ID.

Backend `userId` is a verified canonical `users.id`, never an internal analytics person UUID.
Immutable provider identities bridge existing lifecycle identities where available; unresolved events retain an opaque `anonymousId` with `identity_quality: unresolved` instead of being dropped or guessed from a handle/email alone.
`attribution_role` distinguishes a proven human actor from a signed run owner; ownership is not proof of human initiation.
Tool reports lack invocation provenance and may remain anonymous; use their runtime join to request activity when available.

The existing outbox attempts to schedule minimized lifecycle payloads after ingestion; optional Segment scheduling failures are logged without failing or retrying successful PostgreSQL analytics delivery.
Runtime activity capture is also fail-soft and schedules directly into the job queue.
Segment network delivery uses a separate two-slot worker on that queue, isolated from operational job capacity, with bounded retries and stable `messageId` values.
Each reported tool result adds one minimized job; scheduling is best effort, and database contention or export backlog can still delay or lose capture.
Retries are not exactly-once delivery, an unlimited retry guarantee, or historical backfill; enabling a key does not replay already acknowledged events.

Both paths export only allowlisted property keys and values.
Raw URLs, queries, referrers, page titles, prompts, local paths, repository names, tool content, raw errors and SDK campaign enrichment are excluded; backend subject identifiers are opaque.
IP context is set to `0.0.0.0`; disable any unwanted IP enrichment in the destination as well.
No cross-product identity linking is performed.

## 1. Create the deployment

You need the deployment's public URL before the GitHub App can be created, so create the deployment first.
Its initial revision may remain stopped until step 6, when you configure the GitHub and Slack variables plus a required login allowlist.

**LangGraph Platform.**
Connect the repository to a new deployment in LangSmith → Deployments.
The image build bundles the dashboard (the `dockerfile_lines` in `langgraph.json`), so the deployment URL serves the UI at `/` and the API beneath it; a failed UI build is logged and the backend still deploys.
The platform injects `LANGSMITH_API_KEY`, `LANGSMITH_TRACING`, and `LANGSMITH_PROJECT`.
You will set the environment variables in step 6.

**Standalone Docker.**
The root `Dockerfile` builds a production LangGraph API server image (not the sandbox image):

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
  -e LANGGRAPH_URL="https://<your-backend-url>" \
  open-swe
```

The example assumes Postgres and Redis run on the Docker host; `--add-host` is what makes `host.docker.internal` resolve on a plain Linux Docker Engine.
If they run as containers, drop the flag and point `DATABASE_URI` / `REDIS_URI` at their service names on a shared network.
Add the standalone Agent Server requirements: `DATABASE_URI`, `REDIS_URI`, `LANGSMITH_API_KEY`, and `LANGGRAPH_CLOUD_LICENSE_KEY`.
Expose port `8000` through your ingress, and do not use scale-to-zero hosting: background runs rely on the Redis- and Postgres-backed workers staying up.
Bundle the dashboard by building it (`make build-dashboard`) before `docker build`, or set `DASHBOARD_STATIC_DIR` to a directory holding the build.

**Authentication.**
The three `LANGGRAPH_AUTH_TYPE` lines matter.
The standalone image defaults to `noop`, which leaves the LangGraph API (`/threads`, `/runs`, `/assistants`, `/store`) open to anyone who can reach the port; only the dashboard API (session cookie) and the webhooks (signatures) check anything themselves.
`langsmith` makes the LangGraph API require a LangSmith API key from your workspace on every call, which is what LangGraph Platform does and what Robin's own calls already send (`LANGSMITH_API_KEY`); the webhooks and dashboard API are custom routes and keep working as before.
It needs `LANGSMITH_AUTH_ENDPOINT` (your LangSmith API URL) and `LANGSMITH_TENANT_ID` (the workspace id, shown under **Settings → Workspaces** in LangSmith).
Use `noop` only on a private network behind a gateway that does the authentication for you.

Either way, the URL browsers and webhooks use from here on is `<URL>`: `https://<name>-<hash>.<region>.langgraph.app` on the platform, or your ingress hostname in front of the container.

**Analytics storage.**
When `POSTGRES_URI` is available to application code, startup migrations create and update the `open_swe_analytics` schema in that database.
The database role must be allowed to create the schema and manage its tables and indexes.
Deployment identity and collection-start metadata are persisted automatically; no additional analytics settings or worker deployment are required.
`POSTGRES_URI` is required: startup migrations create the repository and pull-request records, user identities, flat repository and Slack integration bindings, global settings, and execution-image state.
A user row is created only for a GitHub login that passes the dashboard allowlist; `CONFIGURED_ADMINS` also synchronizes admin flags at startup.
The server refuses to start without database access.
Startup logs identify the setting and schema without printing the connection string.

Analytics opens its own pooled SQLAlchemy/asyncpg connection to PostgreSQL.
It does not provision or host a database.
On LangGraph Platform, verify that the deployment exposes `POSTGRES_URI` to custom application code; having a working Agent Server Store does not by itself establish that access.
For standalone deployments, explicitly supply `POSTGRES_URI` from the same database secret used for Agent Server's `DATABASE_URI`.
Analytics reads only `POSTGRES_URI`, so setting `DATABASE_URI` alone does not enable it.
PostgreSQL URLs using `postgres://` or `postgresql://` are accepted, and `sslmode` is translated for asyncpg.
Preserve the deployment's TLS settings when mapping the connection.

After deployment, look for `Analytics database initialized` in startup logs.
A signed-in administrator can then open `/dashboard/api/analytics/readiness` and check `configured: true` and `ready: true`; `/dashboard/api/analytics/outbox-status` reports pending and failed deliveries.
These endpoints are added by the analytics API integration.
Startup failures leave the app running, so a healthy homepage alone does not verify analytics.
If readiness fails, check that the role can create the analytics schema and owns its tables for future migrations.
No deployment UUID or collection timestamp needs to be supplied.

`collection_started_at` is set when the first event is successfully captured, not at startup.
Reports read event-derived SQL tables and include `last_processed_at` plus indicators for queued or failed deliveries.
Collection start and processing progress do not guarantee complete coverage: events can arrive late, and sources that have not been connected to analytics are absent.
The Usage leaderboard, reviewer statistics, and PR outcomes all read these PostgreSQL projections.
There is no analytics Store fallback or historical import.
`reporting_cutover_at` is persisted once when the complete reporting integration first starts, and survives restarts.
Reports exclude activity before this date; “All time” means since this cutover.
The Usage page displays the date explicitly.
Collection can begin earlier while the stack is rolling out, so `collection_started_at` and the reporting cutover describe different milestones.

## 2. LangSmith API key

Create a [LangSmith](https://smith.langchain.com/) API key under **Settings → API Keys** and save it as `LANGSMITH_API_KEY`.
LangGraph Platform injects it into the deployment for you, along with `LANGSMITH_TRACING` and `LANGSMITH_PROJECT`; standalone deployments set it themselves.

The same key is used for tracing, sandboxes, and trace links.
Trace links find your workspace through the key and the project by name, so no tenant or project ids are needed (`LANGSMITH_TENANT_ID` remains an override).
Sandboxes use the global execution image, falling back to the configured base or LangSmith's root snapshot when no image is configured.
The root ships `git`, `gh`, Python, `uv`, and Node; admins configure additional tooling and publish a shared image when needed (see [Defaults and images](reference/execution.md#shared-defaults-and-images)).
Other sandbox providers are covered in [CUSTOMIZATION.md](CUSTOMIZATION.md).

## 3. Create a GitHub App

Robin authenticates as a [GitHub App](https://docs.github.com/en/apps/creating-github-apps) to clone repositories, push branches, open pull requests, and sign users in to the dashboard.

Go to **GitHub Settings → Developer settings → [GitHub Apps](https://github.com/settings/apps) → New GitHub App** and fill in:

- **GitHub App name**: **Robin Agent** (`robin-agent`) for the shared deployment, subject to GitHub availability checks; use a distinct owned name for local or parallel deployments.
  The product and Slack bot remain Robin.
- **Callback URL**: `<URL>/dashboard/api/auth/callback`.
  GitHub Apps take several, one per line; for [local development](DEVELOPMENT.md) add `http://localhost:2024/dashboard/api/auth/callback`.
- **Request user authorization (OAuth) during installation**: off
- **Webhook URL**: the MDA deployment's `<deployment-url>/channels/github/events`, which `mda deploy` sets ([mda/README.md](../mda/README.md#github)), **Webhook secret**: the output of `openssl rand -hex 32`, which `mda deploy` hands the deployment (`MDA_GITHUB_WEBHOOK_SECRET`)
- **Repository permissions**:
  - Contents: Read & write
  - Pull requests: Read & write
  - Issues: Read & write
  - Checks: Read & write — reports a "Robin Review" check run on PRs while an auto-review runs and lets `/baby-sit` read third-party CI conclusions.
    Without it, check-run creation fails (logged, best-effort), reviews still work, and `/baby-sit` fails closed when it cannot read the complete check set.
  - Commit statuses: Read-only — required for `/baby-sit` to evaluate the complete PR status set, including integrations that report via legacy commit statuses.
  - Code scanning alerts: Read & write — lets Robin inspect, dismiss, and reopen code-scanning alerts.
    Existing installations must approve the expanded permission before the MDA GitHub token can receive it.
    Source-code changes still use the Contents permission above; Dependabot and secret-scanning alerts require separate permissions.
  - Actions: Read-only — optional for CI diagnostics and log access.
    Grant **Read & write** only to enable `/baby-sit` to rerun evidence-backed flaky GitHub Actions jobs; existing installations must approve the elevation, and the token could then also cancel or delete runs.
  - Workflows: Read & write — lets Robin push branches containing explicitly requested GitHub Actions workflow changes.
  - Metadata: Read-only
- **Organization permissions**: Members: Read-only — verifies org membership for dashboard login and LangSmith trace-tool access when `ALLOWED_GITHUB_ORGS` is set.
  Without it that check fails closed.
- **Subscribe to events**: Issue comment, Pull request review, Pull request review comment, Check run, Check suite, Workflow run (the last three give `/baby-sit` immediate failure detection), and Status (optional; legacy commit-status integrations).

Click **Create GitHub App**, then collect from its settings page:

- **App ID** → `GITHUB_APP_ID`
- **Client ID** (starts with `Iv`) → `GITHUB_APP_CLIENT_ID`
- **Client secrets → Generate a new client secret** → `GITHUB_APP_CLIENT_SECRET`
- **Private keys → Generate a private key** downloads a `.pem`; its whole contents, BEGIN and END lines included → `GITHUB_APP_PRIVATE_KEY`

Finally **Install App** in the sidebar: pick the account and the repositories Robin may work in.
The number at the end of the resulting URL, `https://github.com/settings/installations/<id>` (or `/organizations/<org>/settings/installations/<id>`), is `GITHUB_APP_INSTALLATION_ID`.

Give each deployment its own GitHub App, or at least a distinct mention handle (`OPEN_SWE_MENTION_TAGS`, see [Allowlists](#repository-allowlists-mention-handles-and-user-mapping)) when several share a GitHub organization.

## 4. Model providers and API keys

Robin calls models through [LangChain](https://python.langchain.com/) chat models named `provider:model`, using providers supported by the gateway integration.
All model calls use the LangSmith LLM Gateway.
Configure provider credentials in your LangSmith workspace's Provider Secrets, not Robin's environment.
Set `LANGSMITH_GATEWAY_API_KEY` to a LangSmith key with `gateway:invoke`; if unset, Robin uses `LANGSMITH_API_KEY` (the platform-provided key may lack this permission).
`LANGSMITH_GATEWAY_BASE_URL` points at a regional or self-hosted gateway.
Routing cannot be disabled.
Unsupported providers or missing gateway credentials fail closed.
OpenAI uses the Responses API.

**Which model runs.**
The deployment default is `openai:gpt-6.1-sol` with medium reasoning effort; override it with `LLM_MODEL_ID` (`provider:model`) and `LLM_REASONING_EFFORT` (`low`, `medium`, `high`, `max`), and name a `LLM_FALLBACK_MODEL_ID` for when the primary provider fails.
Admins set global model and effort defaults under **Defaults** (`/settings`).
Users can override the model for an individual thread, for example by asking the agent to switch to the performance model there.
Model ids and their providers are described in [CUSTOMIZATION.md](CUSTOMIZATION.md).

**Other API keys.**
`EXA_API_KEY` (from [dashboard.exa.ai](https://dashboard.exa.ai)) enables the web search tool; with `MDA_EXA_CONNECTION`, runs read the key from the MDA deployment's connection instead ([mda/README.md](../mda/README.md#configure)).
`REVIEWER_OUTCOMES_DATASET` names the LangSmith dataset the reviewer records finding outcomes in (default `openswe-reviewer-outcomes`).

## 5. Create the Slack app

Robin answers `@`-mentions in Slack and posts its progress there, and Slack is how most teams start runs.
The app posts events to your deployment's URL, so it needs the same public URL as the GitHub App.

1. Go to [api.slack.com/apps](https://api.slack.com/apps) → **Create New App** → **From a manifest**, and paste the manifest below with `<your-url>` replaced by the hostname of `<URL>`, for example `my-open-swe-abc123.us.langgraph.app`; the manifest already supplies the `https://`.

<details>
<summary>Slack App Manifest</summary>

```json
{
    "display_information": {
        "name": "Robin",
        "description": "Enables Robin to interact with your workspace",
        "background_color": "#000000"
    },
    "features": {
        "app_home": {
            "home_tab_enabled": false,
            "messages_tab_enabled": true,
            "messages_tab_read_only_enabled": false
        },
        "bot_user": {
            "display_name": "Robin",
            "always_online": true
        }
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
                "search:read.files",
                "search:read.public",
                "search:read.users",
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
                "channel_unarchive",
                "channel_deleted",
                "group_rename",
                "group_archive",
                "group_unarchive",
                "group_deleted",
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

1. Optionally, turn on **Agents & AI Apps** in the app settings so Slack attaches a search grant to each mention and DM; the agent's `slack_search` tool needs it to search public channels.
1. Install the app to your workspace.
1. Add to the environment (step 6):

```bash
SLACK_BOT_TOKEN=""        # OAuth & Permissions → Bot User OAuth Token (xoxb-...)
SLACK_SIGNING_SECRET=""   # Basic Information → App Credentials → Signing Secret
SLACK_APP_ID=""           # Basic Information → App ID (A...); Incidents accepts events only from this app
SLACK_BOT_USER_ID=""      # the bot's member id (open the bot's profile in Slack → ⋮ → Copy member ID)
SLACK_BOT_USERNAME=""     # the bot's handle, e.g. robin (use its actual configured handle)
```

Set `SLACK_ALERTS_CHANNEL_ID` to the channel ID for operational alerts and invite the bot to that channel.
If unset, alerts stay in server logs and are not sent to Slack.
Slack delivery is limited to one operational alert per minute per channel across replicas.

Both Slack URLs must point at the Robin deployment, and Block Kit buttons only work with Interactivity enabled and pointed at `/webhooks/slack/interactivity`.
Slack messages are routed to the thread's repository, a `repo:owner/name` token in the message, or the team default repository.
Robin refuses Slack Connect channels (`is_ext_shared`) and fails closed when it cannot verify a channel.

`files:read` lets Robin download files attached to a message (images, archives, logs, CSVs) and stage them in the thread's sandbox, where the agent reads them by path; images also reach the model as vision input.
Existing installations must add the scope in **OAuth & Permissions** and reinstall the app before attachments reach the agent; without it, uploads stay invisible and only the message text is used.

Slack verifies the events Request URL the first time it can reach it; if the backend is not up yet when you create the app, use **Retry** under **Event Subscriptions** after step 7.

## 6. Set the environment variables

```bash
LANGGRAPH_URL="<URL>"                 # the deployment's own URL
LANGSMITH_API_KEY=""                  # step 2; injected by LangGraph Platform
LANGSMITH_TRACING="true"              # injected by LangGraph Platform
LANGSMITH_GATEWAY_API_KEY=""          # step 4: LangSmith key with gateway:invoke

GITHUB_APP_ID=""                      # step 3
GITHUB_APP_CLIENT_ID=""
GITHUB_APP_CLIENT_SECRET=""
GITHUB_APP_PRIVATE_KEY="-----BEGIN RSA PRIVATE KEY-----\n...\n-----END RSA PRIVATE KEY-----"   # one line with \n between the PEM lines, or the multi-line value your platform accepts
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

MDA_RUNTIME_URL=""                    # the Managed Deep Agents deployment of mda/; required
MDA_RUNTIME_API_KEY=""                # LangSmith key to call it with; defaults to LANGSMITH_API_KEY
MDA_WORKSPACE_ID=""                   # its LangSmith workspace; defaults to this backend's
MDA_DEPLOYMENT_ID=""                  # its deployment id, once `mda deploy` registered the Slack app: the bot token is then read from Agent Auth, not SLACK_BOT_TOKEN
MDA_LANGSMITH_PROJECT=""              # its LangSmith project (the deployment name); defaults to LANGSMITH_PROJECT
REMOTE_RUNTIME_TOKEN_SECRET=""        # openssl rand -hex 32     (signs the run tokens it calls back with); required
```

Every Robin thread runs its model loop on a [Managed Deep Agents](https://docs.langchain.com/langsmith/python/managed-deep-agents-overview) deployment of the project in `mda/`, and the backend refuses to start without one.
Deploy it with `mda deploy` as [mda/README.md](../mda/README.md#build-and-deploy) describes, with `OPEN_SWE_BACKEND_URL` set to this deployment's URL.

On LangGraph Platform, set them under the deployment's environment variables; saving rolls out a new revision.
With Docker, put them in the file you pass as `--env-file`.
`DASHBOARD_BASE_URL` and `DASHBOARD_API_BASE_URL` are not needed: they default to `LANGGRAPH_URL` because the dashboard is served from the same origin.

## 7. Verify it works

**Dashboard.**
Open `<URL>`, click **Sign in with GitHub**, and you should land logged in.
With your login in `CONFIGURED_ADMINS`, **Defaults** (`/settings`) and the **Administration** pages (Integrations, Users, …) appear.
Set **Admin → Defaults → Default repository** so runs that name no repository have somewhere to go.
Start a task from the composer.
New cloud sandboxes use the global execution image, or the configured base/root snapshot when no image is set.
For extra preinstalled toolchains, an admin can configure reproducible scripts in **Defaults**, then ask an admin thread to run `rebuild_execution_image(kind="full")`.
Alternatively, explicitly provision that thread's sandbox and request `publish_execution_image()`; publication changes future sandboxes without stopping its source.
Saving Defaults alone does not build an image.

**Slack.**
Invite the bot to a channel and mention it: `@Robin what's in the repo?`.
It replies in a thread.
Public runs use the deployment GitHub App for agent operations, and user-owned PRs are opened as the thread's initiating GitHub user.
Link the Slack user to a GitHub login before starting the thread, either by signing in to the dashboard once or through [Sign in with Slack](#slack-sign-in).

**GitHub.**
GitHub-triggered conversations are public.
Agent GitHub operations use the App installation identity, while PRs use the initiating commenter's OAuth.
The commenter must have a linked account; an unmapped commenter is skipped with a warning in the server log.
Comment `@robin-agent what files are in this repo?` on an issue in a repository where the App is installed.
Within a few seconds you should see a 👀 reaction, a run in your LangSmith project, and a reply comment.
GitHub lists every delivery and its response under the App's **Advanced** tab.

**Defaults and integration bindings.**
Every run uses global shared defaults, with personal repository preferences and explicit thread choices where supported.
Admins manage repository and Slack bindings under **Integrations**; these do not select different sandbox images or settings.
Adding a repository binding does not grant GitHub Actions permission to start threads: enable **Can start threads** explicitly.
See [Defaults, routing, and sandbox images](reference/execution.md) for the tools, API, and access model.

---

## Optional add-ons

Open a section when you want that feature; everything above keeps working without it.

<details id="slack-sign-in">
<summary><strong>Slack: "Sign in with Slack" linking</strong></summary>

**"Sign in with Slack" account linking.**
Lets a user link their Slack identity to their GitHub login from **Settings → Connections**, so Slack-triggered runs resolve to the right GitHub user through Slack's verified claims.
Without it, Slack senders stay unlinked until they connect from **Settings → Connections**; **Admin → Users** shows who has.
The manifest already registers the OIDC redirect; make sure the `openid`, `email`, and `profile` user scopes are available, then set `SLACK_CLIENT_ID` and `SLACK_CLIENT_SECRET` from **Basic Information → App Credentials**, and optionally `SLACK_TEAM_ID` (`T...`) to restrict linking to one workspace.
When they are unset the link is simply hidden.

**Listening channels.**
Admins configure listening channels and their editable instructions under **Integrations** (`/admin/integrations`), or with `bind_slack_channel(channel_id=..., prompt=...)`.
Robin must have joined the internal channel; externally shared channels are not eligible.
The prompt can describe what Robin should respond to, a DRI, escalation guidance, and whether to investigate only or implement requested fixes.
Slack topics, purposes, and descriptions no longer configure behavior or repository defaults; existing description instructions are not migrated.
Prompts accept up to 32,000 characters; an empty prompt uses standard response behavior.
Updating the binding replaces its instructions without changing its channel or existing conversations.
Jev uses those instructions to decide whether an untagged message needs a response, a reaction, or no action.
A responding top-level message starts an agent thread and replies continue it without tagging Robin; ordinary untagged replies queue behind an active run.
The current instructions reach every turn, including explicit mentions and follow-ups in existing threads.
Deploy the UI, backend, and runtime together.
The listening-channel migration clears existing channel bindings; re-add channels and enter their instructions after deployment.
Existing conversations are not deleted.
Listening channels can answer ordinary questions from unlinked humans without borrowing personal credentials.
Implementation and PR requests require the requester to sign in and link Slack and their own GitHub account in **Settings → Connections**.
Public listening-channel PRs remain drafts and require the requester's linked GitHub authorization and repository access, without falling back to Robin.
Instructions cannot grant credentials or tool permissions.
Slack-triggered automations remain useful for separate event-driven tasks; they do not replace listening channels' ongoing conversation and reply handling.

To enable private channels, update the Slack app with the current manifest and reinstall it so Slack sends the `message.groups` event (the base manifest already includes the `groups:history` scope).

Existing Slack app installations must update their event subscriptions; saving the dashboard setting alone does not change them.

**Native Stop.**
Update existing Slack apps with the generated manifest (or add the `agent_session_stopped` bot event) and enable Agent view.
Robin marks ordinary threads as processing agent sessions so Slack shows Stop; this replaces the rotating working status there.
Switching to Agent view is irreversible and app-wide; apps that stay on the assistant view keep the rotating status without Stop.
Stop uses the linked clicker's dashboard cancellation permissions, interrupts current work, and clears the indicator without resolving the conversation or undoing completed actions.
Other participants' queued runs and pending follow-ups are preserved and may continue; a later message can also continue the thread.
Anyone who can read a bot-triggered thread can stop it; it otherwise stays read-only.

</details>

<details id="incidents">
<summary><strong>Incident channels</strong></summary>

Robin can join new incident channels on its own and start investigating.

1. Install or reinstall the Slack manifest above and set `SLACK_APP_ID` from **Basic Information → App ID**.
   Joining needs the `channel_created` and `channel_rename` events and the `channels:join` scope.
1. In **Admin → Incidents**, set a channel prefix such as `inc-`, optionally exclude channels or pick a model, and enable it.

When a public internal channel is created with the prefix, or renamed into it, Robin joins, posts one message, and about a minute later starts a system-owned thread under that message with a prompt to investigate the issue.
From then on the thread is an ordinary Robin Slack thread: mention Robin there to follow up.
Optionally give the agent an incident tracker by adding its MCP server, for example incident.io at `https://mcp.incident.io/mcp`, to Robin's LangSmith Managed Tools bot kit in LangSmith; see [MCP servers](CUSTOMIZATION.md#mcp-servers).

</details>

<details id="api-keys">
<summary><strong>Machine callers: API keys and GitHub Actions</strong></summary>

LangSmith API keys and GitHub Actions workflows can start Robin threads without a browser session.
They post the same command the dashboard posts, to `POST /dashboard/api/threads/<thread_id>/commands`.

Every creating command names the kind of thread it wants in `config.configurable.thread_type`:

| `thread_type` | Who may ask for it |
|---|---|
| `system` | the bot (Workspace Admin service keys), federated workflows, and admins |
| `workspace` | any signed-in person, or their personal access token |
| `private` | any signed-in person, or their personal access token |

**LangSmith API keys.**
Robin mints no keys of its own.
Send a LangSmith key as `Authorization: Bearer <key>` or `X-Api-Key: <key>`, and Robin asks LangSmith (its tenant `/auth` endpoint) who it is, pinned to the LangSmith workspace Robin signs people in to: the workspace of the deployment's own `LANGSMITH_API_KEY`.
A key that cannot act in that workspace is refused, whatever else it can reach.
LangSmith's answer is reused for up to 60 seconds, so a key revoked in LangSmith stops working here within a minute.

- A **personal access token** (`lsv2_pt_…`) acts as its person: the Robin user who signed in with that LangSmith account, with the same access as their session.
  Their Robin login must still be live (the same check a session gets), because their runs call models on it; otherwise the token gets a 403 asking them to sign in again.
- A **service key** (`lsv2_sk_…`) that holds `workspaces:manage` in that workspace (a Workspace Admin key) acts as the workspace's **bot**, a machine that may only start **system** threads: owned by the deployment rather than a person, public, and carrying no GitHub user, so nothing it does borrows anyone's credentials.
  Every service key is the same bot, so a rotated key still reads the threads the old one started.
  The bot's threads use global execution defaults.

Service keys without `workspaces:manage` (for example ones made for tracing or CI) get a 403: the bot can start threads in any repository the GitHub App reaches, so only a key a workspace admin made for it may act as it.
Robin's own deployment keys (`LANGSMITH_API_KEY`, `LANGSMITH_GATEWAY_API_KEY`, `MDA_RUNTIME_API_KEY`) are refused, as are tracing keys and legacy `ls__` keys.

```bash
curl -X POST "<URL>/dashboard/api/threads/$(uuidgen | tr 'A-Z' 'a-z')/commands" \
  -H 'X-Api-Key: lsv2_sk_…' -H 'Content-Type: application/json' \
  -d '{"id": 1, "method": "run.start", "params": {
        "input": {"messages": [{"type": "human", "content": "Upgrade the linter and open a PR"}]},
        "config": {"configurable": {"thread_type": "system", "repo": "acme/api"}}}}'
```

**GitHub Actions, with no stored secret.**
A workflow asks GitHub for an OIDC token naming its repository, ref and workflow, and presents that instead of a key.
Robin verifies GitHub's signature against its published keys, checks the audience, and then checks its own trust policy: the repository must have an integration binding with explicit `may_start_threads=true`.
Enable **Can start threads** for the repository under **Integrations**, or use `bind_repository(repo="owner/name", may_start_threads=True)`.
Binding a repository never implies the grant.

Set `GITHUB_OIDC_AUDIENCE` to the value your workflows request; it defaults to `DASHBOARD_BASE_URL`.

```yaml
permissions:
  id-token: write
steps:
  - id: token
    run: |
      echo "value=$(curl -sH "Authorization: bearer $ACTIONS_ID_TOKEN_REQUEST_TOKEN" \
        "$ACTIONS_ID_TOKEN_REQUEST_URL&audience=<URL>" | jq -r .value)" >> "$GITHUB_OUTPUT"
  - run: |
      curl -X POST "<URL>/dashboard/api/threads/$(uuidgen | tr 'A-Z' 'a-z')/commands" \
        -H "Authorization: Bearer ${{ steps.token.outputs.value }}" \
        -H 'Content-Type: application/json' \
        -d '{"id": 1, "method": "run.start", "params": {
              "input": {"messages": [{"type": "human", "content": "Nightly build failed, investigate"}]},
              "config": {"configurable": {"thread_type": "system"}}}}'
```

A workflow that names no `repo` works in its own repository.
A machine caller reads back only the threads it started, through `GET /dashboard/api/threads` and `GET /dashboard/api/threads/<id>`, and their transcripts, through `GET /dashboard/api/threads/<id>/transcript`, `/transcript/turns`, `/transcript/events`, `/transcript/tool-calls/<id>/output` and `/transcript/attachments/<id>`; unknown, revoked and expired credentials all answer `401`.

</details>

<details id="linear">
<summary><strong>Linear</strong></summary>

Robin works in Linear as an app, through the Linear channel of its Managed Deep Agents deployment (`mda/channels/linear.py`).
Mentioning the app on an issue, or delegating the issue to it, opens an agent session.
The agent answers there, a reply in the session continues the conversation, and **Stop** cancels the run.
Each session is its own conversation and thread; mentioning the app again on the same issue opens a new one.
The dashboard shows these threads read-only: continue them in Linear.

1. Pick a secret (`openssl rand -hex 32`) and set it as `MDA_SERVICE_SECRET` in both `mda/.env` and the backend's environment.
   The deployment signs each Linear event it hands the backend with it, as Standard Webhooks does, and the backend answers only calls whose signature it verifies.
1. Run `mda deploy` in `mda/`.
   Deploy sets up the Linear app, which takes a Linear workspace admin: it opens Linear's new-app page filled in with the app's name, redirect URI, webhook URL and events, then asks for the app's client ID, client secret and webhook signing secret, and the admin approves the install.
   The deployment checks each delivery's signature with that secret; the backend needs no Linear secret.
1. Set the global default repository under **Defaults**.
   Add a `repo:owner/name` token or GitHub URL to the message that mentions the app when the issue belongs to another repository.

The session's answer and errors need no tool.
For Robin to read and change issues beyond the session, add Linear's tools to the LangSmith Managed Tools bot kit; see [MCP servers](CUSTOMIZATION.md#mcp-servers).

Issue events still reach automations and `listen_events`: the channel forwards them to the backend.

**Verify:** mention the app on an issue with `what files are in this repo?`, adding `repo:owner/name` when needed.

</details>

<details id="object-storage">
<summary><strong>Object storage for app files</strong></summary>

Apps keep files beside their documents, such as Design QA's screenshots.
Files of at most 256 KiB stay in PostgreSQL; larger ones, up to 8 MiB, go to an S3-compatible bucket when one is set up, and otherwise stay in PostgreSQL too.
A bucket is optional: set one up when files would grow the database more than you want.
On GCS, Robin uses the XML API with an HMAC key, so it needs no Google credentials file:

1. Create a bucket, and a service account with **Storage Object User** (`roles/storage.objectUser`) on it.
1. Create an HMAC key for that service account (`gcloud storage hmac create SERVICE_ACCOUNT_EMAIL`).
1. Set:

   ```bash
   OBJECT_STORE_URL="https://storage.googleapis.com"
   OBJECT_STORE_BUCKET="open-swe-files"
   OBJECT_STORE_ACCESS_KEY_ID=""      # the HMAC key's access ID
   OBJECT_STORE_SECRET_ACCESS_KEY=""  # its secret
   ```

S3, R2 and MinIO work the same way with their own endpoint and keys; on S3, also set `OBJECT_STORE_REGION` to the bucket's region.
The key needs to read, write, delete and list objects under `plugin-attachments/` in that bucket.
An hourly job deletes the objects no attachment names any longer, once they are a day old.

</details>

<details id="review-apps">
<summary><strong>Apps in reviews, such as Design QA screenshots</strong></summary>

An app's [review step](PLUGINS.md#review-steps) has pull request reviews do work for it; Design QA's screenshots a frontend pull request's preview beside its base ([reference](../plugins/design-qa/README.md)).

1. Publish the app in **Settings → Apps** (for Design QA, build `plugins/design-qa` with `pnpm --filter openswe-plugin-design-qa build` and upload `plugins/design-qa/bundle/index.html`), and mark it listed so others can add it.
   To put it in everyone's sidebar, an instance admin turns it on under **Defaults → Apps for everyone**, and names the repositories it reads for everyone, such as the one its reviews capture.
1. For apps whose review step uses the browser, an instance admin fills in **Defaults → Review browser**.
   List the domains, each with its subdomains, the review's browser may sign in to: your previews' and their base site's, never production.
   Optionally, name the bot kit's MCP tool that returns a browser session, as `<connection>/<tool>`.

   The deployment's LangSmith workspace needs the LangSmith browser beta, as the coding agent's browser does.
   Browser sessions install only on LangSmith sites, so a review signs in to LangSmith previews; it browses any other preview signed out.
1. Add the app under **Defaults → Apps in reviews**.
   Only reviews of private repositories follow it.
   Reviews follow the release that was newest when it was added; when its owner releases again, **Update** there moves reviews to the new one.
1. Under **Preview checks** there, name the checks that pass only once a pull request's preview serves its head commit, one per line, as the pull request's checks list shows them, such as a deploy workflow's `wait-for-dns` job.
   A review usually finishes before the preview is up; when one of these passes for a pull request, Robin does the apps' review steps against the preview.
   Pick a check that is skipped when no preview is built, and the GitHub App must receive `check_run` events.
1. Say where the previews' base site is in the shared review guidelines, so the review knows where to take "before" screenshots.

</details>

<details id="dashboard-on-its-own-origin">
<summary><strong>Dashboard on its own origin (separate frontend deployment)</strong></summary>

The bundled dashboard needs none of this.
Read on only if the dashboard is deployed separately from the backend.

**A separate frontend deployment.**
The `ui/` app also builds to a Nitro server (`ui/Dockerfile`) that renders on request.
Set its `DASHBOARD_API_URL` to the backend URL; browser requests to `/dashboard/api/*` and webhook deliveries to `/webhooks/*` are proxied there, and server renders forward the `osw_session` cookie.
Set `DASHBOARD_BASE_URL` and `DASHBOARD_API_BASE_URL` on the backend to the frontend origin and register `<frontend origin>/dashboard/api/auth/callback` on the GitHub App.
To have the browser call the backend cross-origin instead, build the UI with `VITE_DASHBOARD_API_BASE_URL` set to the backend origin, keep `DASHBOARD_API_BASE_URL` on the backend origin, and add the frontend origin to `DASHBOARD_ALLOWED_ORIGINS`; the session is then resolved on the client after hydration.

**Mount prefix.**
If the server runs under a LangGraph `http.mount_prefix`, the Platform image builds the UI for that prefix automatically; locally pass it to the build (`DASHBOARD_BASE_PATH=/<prefix>/ make build-dashboard`) and keep `LANGGRAPH_URL` on the mounted URL.

</details>

<details id="datadog-rum">
<summary><strong>Datadog RUM</strong></summary>

Set `DD_RUM_APPLICATION_ID` and `DD_RUM_CLIENT_TOKEN` on the backend to enable RUM for the web and downloaded desktop UI without rebuilding them.
Optionally set `DD_RUM_SITE` (default `us5.datadoghq.com`); both identifiers must be set or RUM stays disabled.
The backend returns these public values from `/dashboard/api/analytics/config`: use only a browser client token, never a Datadog API or application key.
A complete pair of `VITE_DATADOG_APPLICATION_ID` and `VITE_DATADOG_CLIENT_TOKEN` at build time takes precedence over the backend settings; `VITE_DATADOG_SITE` applies to that pair.
Other optional build settings: `VITE_DATADOG_SERVICE` (default `open-swe-dashboard`), `VITE_DATADOG_VERSION`, `VITE_DATADOG_SESSION_SAMPLE_RATE` (default `100`), and `VITE_DATADOG_SESSION_REPLAY_SAMPLE_RATE` (default `0` for runtime configuration, `100` for build-time identifiers).
Review replay sampling before enabling it; changing a build setting requires rebuilding the UI.
The environment comes from the backend's `DD_ENV` at runtime, shared with Segment and analytics (default `production`); `ANALYTICS_ENVIRONMENT`, `VITE_DATADOG_ENV`, and the Vite mode no longer select it.
RUM skips initialization if the backend telemetry configuration cannot be loaded.
Session Replay masks all content and telemetry strips query strings and fragments.
`VITE_` values are public in the bundle; use a client token, never an API or application key.
The dashboard also reports three custom duration vitals, `thread_load`, `thread_list_load`, and `agent_run`, with their phase breakdown in the vital context (RUM Explorer: `@type:vital @vital.name:thread_load`); see [docs/DEVELOPMENT.md](DEVELOPMENT.md#profiling-thread-load-and-streaming) for what they measure.

</details>

<details id="repository-allowlists-mention-handles-and-user-mapping">
<summary><strong>Repository allowlists, mention handles, and users</strong></summary>

**Mention handles.**
The handles this deployment answers to default to `@robin-agent,@openswe,@open-swe,@openswe-dev`; set `OPEN_SWE_MENTION_TAGS` to change them.
Legacy handles remain compatible.
Standalone handles match case-insensitively; email addresses and longer handles such as `@robin-agent-staging` do not trigger the agent.
These are accepted text triggers, not a change to any external GitHub App login.
The App owner must register or rename the App separately; bare `@robin` belongs to an unrelated account and is not a default trigger.
Set `EXTRA_INTERNAL_BOT_LOGINS` (e.g. `openswe-staging[bot]`) to treat other Robin deployments' comments as internal rather than untrusted.

**Allowlists.**

```bash
ALLOWED_GITHUB_ORGS="langchain-ai,anthropics"                        # org members allowed to log in; all repos in these orgs
ALLOWED_GITHUB_USERS="octocat,hubot"                                 # individual users allowed to log in
ALLOWED_GITHUB_REPOS="some-user/their-repo,another-org/specific-repo"  # specific owner/repo pairs
PUBLIC_REPO_ORG_GATE=""   # single org whose members may trigger runs on *public* repos; empty = no gate
```

Backend startup requires at least one entry in `ALLOWED_GITHUB_ORGS` or `ALLOWED_GITHUB_USERS`; an empty value in both stops the server.
When both are configured, they form a union: dashboard login accepts an explicitly listed user **or** an active member of a listed organization.
Organization membership is verified server-side with the installation token and fails closed on any API error; install the App in every listed organization and grant **Organization → Members: Read-only**.
A GitHub or Linear webhook is accepted if the repo's org is in `ALLOWED_GITHUB_ORGS` **or** the `owner/repo` is in `ALLOWED_GITHUB_REPOS`; both repository allowlists empty allows every installed repository.
For Slack and dashboard requests, `ALLOWED_GITHUB_ORGS` also adds a prompt-level guard: editing a repository outside those orgs requires the user to name it with its full `https://github.com/<owner>/<repo>` URL.
When team LangSmith credentials are connected, every active member of a listed organization can use the read-only LangSmith trace tools, so only list organizations whose full membership may see team-level trace data.

**Users.**
A person gets a `users` row on their first dashboard sign-in, with their GitHub account as its first identity; connecting Slack from **Settings → Connections** adds the Slack account to the same row, which is how a Slack sender resolves to a GitHub login.
**Admin → Users** lists everyone Robin knows.
An unlinked person who tags Robin in Slack gets a run with the GitHub App's installation permissions and a "link your GitHub account" prompt; signing in and connecting Slack completes it.
Records from the older Store-backed user mapping are imported into `users` on the first startup that finds them, then deleted.

**Default repository.**
Runs that name no repository use the global default repository under **Defaults**, seeded from `DEFAULT_REPO_OWNER` / `DEFAULT_REPO_NAME` when set; `SLACK_REPO_OWNER` / `SLACK_REPO_NAME` are a Slack-only fallback.

</details>

<details id="rotating-token_encryption_key">
<summary><strong>Rotating <code>TOKEN_ENCRYPTION_KEY</code></strong></summary>

`TOKEN_ENCRYPTION_KEY` accepts a single Fernet key or a comma- or newline-separated **ordered list, most recent first**.
Writes use the first key; reads try every key in order.

1. Generate a new key: `openssl rand -base64 32`.
1. Prepend it, keeping the old key second: `TOKEN_ENCRYPTION_KEY="<new_key>,<old_key>"`, and restart.
1. Once every active user has signed in again (each fresh OAuth flow re-encrypts under the new key), drop the old key.
   Anything still encrypted under it fails to decrypt and that user is asked to sign in again.

</details>

## Upgrading from the LangGraph Store

Robin keeps all of its state in PostgreSQL and no longer reads the LangGraph Store.
Releases before this one copied old Store records into PostgreSQL at startup, recording each import's progress in the `store_import` table.
Before upgrading a deployment that predates those imports, run an earlier release until every row of `store_import` has `completed_at` set: this release drops that table and the markers the imports used, and leaves whatever is still in the Store behind.

## Troubleshooting

### Webhook not receiving events

- The URL configured in GitHub, Slack, or Linear must be the deployment's URL; GitHub shows each delivery and its response under the App's **Advanced** tab.
  A new webhook or signing secret takes effect only after the deployment restarts with it; deliveries in between are rejected as `Invalid signature`, and Slack then needs **Retry** on its Request URL under **Event Subscriptions**.
- Enable the right events: Issue comment and the pull request review events for GitHub, `app_mention` for Slack.
  `mda deploy` subscribes the Linear app to the events its channel handles.
- Webhook secrets are required: without `SLACK_SIGNING_SECRET`, every request to `/webhooks/slack` is rejected with 401.
  GitHub and Linear deliveries reach the backend only from the MDA deployment, which verifies them and signs its calls with `MDA_SERVICE_SECRET`, needed on both sides.

### Thread credential scope

Public threads use the GitHub App installation identity for agent GitHub operations.
PRs from user-owned threads are opened with the authenticated current run requester's stored GitHub OAuth token.
For example, if Alice starts a Slack task and Bob triggers a follow-up run asking to create the PR, the new PR is opened as Bob.
Alice remains the task owner, and existing PR authors are unchanged.
If the requester's identity or token is unavailable, PR creation fails without falling back to the task owner or bot.
The requester must have access to the target repository.
Public PR creation first verifies that the target repository is accessible through the configured GitHub App installation.
Ask-channel PRs use the authenticated person who explicitly requests the PR, even in a system-owned Ask thread.
Other system-owned threads, including scheduled automations, open PRs as the GitHub App.
Existing threads without recorded ownership retain bot PR authorship.
System automation runs check repository access with the deployment GitHub App.
They record the automation creator for auditing but do not require that person's OAuth token or inject their GitHub login or email as the agent's execution identity.
Personal automations instead run as their creator, using that person's linked authorization and private thread access.
Admin schedules retain their management tools through authorization tied to the scheduled invocation.
The graph and tools recheck the creator's current admin status; later participants do not inherit that authorization.
Automation management from these system runs also uses deployment credentials.

PR authorship is bound to the saved thread owner outside public listening channels, never inferred from conversation text.
Slack follow-ups carry their own requester identity whether they interrupt or queue behind an active run; that identity does not replace the saved owner outside public listening channels.
Dashboard messages and Slack edits injected into an existing run do not change its identity.
A collaborator outside public listening channels must start their own thread to publish under their own account.
Workflow push approval starts a run with the authenticated actor's identity without changing the saved PR owner.
Public listening-channel PRs use the authenticated requester's own linked GitHub authorization, including background completions of an existing explicit PR request when that requester identity is available.
Missing requester authorization blocks the PR rather than borrowing the thread owner's or App's authorization.
Outside listening channels, PRs use the saved thread owner's identity, including background completions; system-owned and legacy unowned threads retain bot authorship.
GitHub prevents the author from approving their own PR; person-authored PRs otherwise follow the repository's review rules.

Public threads load Robin's LangSmith Managed Tools bot kit and organization skills.
Personal LangSmith Managed Tools, user skills, and user custom instructions are available only in a private thread started by its immutable owner.
The same ownership check applies when a personal managed tool refreshes its credentials at execution time.

Private threads use their owner's stored GitHub OAuth token for server-side GitHub operations and PR creation.
If that token is unavailable, the owner must sign in again; the run does not fall back to the bot.
Sandbox GitHub proxy access continues to use the GitHub App installation token in both kinds of thread.
User identity and membership checks still apply to public runs.

### GitHub authentication errors

- Check `GITHUB_APP_ID`, `GITHUB_APP_PRIVATE_KEY`, and `GITHUB_APP_INSTALLATION_ID`.
  The private key must include the full `-----BEGIN RSA PRIVATE KEY-----` and `-----END RSA PRIVATE KEY-----` lines; in a `.env` file write it as one double-quoted line with `\n` between the PEM lines.
- Make sure the App is installed on the target repositories.

### Dashboard login fails or won't stay logged in

- `500 GITHUB_APP_CLIENT_ID not configured` (or client secret): set `GITHUB_APP_CLIENT_ID`, `GITHUB_APP_CLIENT_SECRET`, and `DASHBOARD_JWT_SECRET`.
- `redirect_uri is not associated with this application`: the App must list `<URL you opened the dashboard on>/dashboard/api/auth/callback`.
  Add it in the App's settings.
- Login redirects but the session does not stick: use `https://` and open the dashboard on `LANGGRAPH_URL` itself.
- `403 CSRF check failed` on saves: the request's `Origin` is neither `DASHBOARD_BASE_URL` (defaults to `LANGGRAPH_URL`) nor in `DASHBOARD_ALLOWED_ORIGINS`.
- Startup fails with `ALLOWED_GITHUB_ORGS or ALLOWED_GITHUB_USERS must be configured`: set at least one nonempty login allowlist.
- Login rejected with an authorization error: add the login to `ALLOWED_GITHUB_USERS`, or configure `ALLOWED_GITHUB_ORGS` and grant the App Organization → Members permission.
- Admin pages 403: add your GitHub login or email to `CONFIGURED_ADMINS`.

### Dashboard shows the LangGraph JSON instead of the UI, or 404s at `/`

- The image has no dashboard build.
  On LangGraph Platform, check the build log for `dashboard build failed`; with Docker, run `make build-dashboard` before `docker build`, or set `DASHBOARD_STATIC_DIR` to a directory holding a build.

### Sandbox creation failures

- `LANGSMITH_API_KEY` must be set and valid, and the workspace must have sandbox access (403 on the sandbox endpoints means it does not; contact LangSmith support).
- Check LangSmith sandbox quotas in your workspace settings.
- `Failed to create sandbox from snapshot '<id>'` means the global execution image or configured base snapshot no longer exists or is not `ready`; an admin should inspect `read_execution_image()` and correct the image/base snapshot configuration in **Defaults**.
  Rebuild or publish only when explicitly requested; do not delete images or recreate sandboxes as automatic recovery.

### Agent not responding to comments

- GitHub: the comment must contain a configured handle (`@robin-agent` or a retained legacy handle by default, case-insensitive), and the commenter must have signed in to the dashboard once; otherwise the log says `No email mapping for GitHub user`.
- Linear: mention the app itself, or delegate the issue to it; a session opens on the issue.
  Slack: the bot must be in the channel and `@`-mentioned.
- Check the server log for webhook processing errors.

### Token encryption errors

- `TOKEN_ENCRYPTION_KEY` must be set to a valid Fernet key (`openssl rand -base64 32`), or an ordered list of them; see [Rotating `TOKEN_ENCRYPTION_KEY`](#rotating-token_encryption_key).
