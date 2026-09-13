# Incidents locally

Incidents lives at `/incidents`. It enrolls public internal Slack channels after a new `channel_created` or `channel_rename` event matches the configured prefix. Each incident keeps a persistent, system-owned conversation on the main `agent` graph, using the normal sandbox, coding tools, subagents, and workspace integrations. The dashboard shows investigations, postmortems, and activity; provider actions use the agent’s configured MCP tools.

## Start the dashboard

From the repository root, run:

```sh
make dev-ui
```

Open [http://localhost:2024/incidents](http://localhost:2024/incidents) and sign in. Vite serves assets on port 3000; the backend and dashboard run on 2024. Responders need an email in `OBSERVABILITY_AUTHORIZED_EMAILS` or admin access through `CONFIGURED_ADMINS`. Incident settings require an administrator.

## Connect a Slack workspace and provider

1. Follow [Incidents setup](INSTALLATION.md#incidents), including the Slack manifest, `SLACK_APP_ID`, and responder access. Deliver events to the signed `/webhooks/slack` endpoint. For local development, use the [webhooks-only tunnel](DEVELOPMENT.md#3-tunnel-for-webhooks); keep the raw LangGraph APIs private.
2. In **Admin → Incidents**, set a literal channel prefix such as `inc-`. The installed bot supplies the workspace identity, and `SLACK_APP_ID` supplies the app identity. Optionally choose a supported model and adjust the analysis limits.
3. For incident.io, configure an existing connection under **Admin → Workspace MCPs**, then enter its name as **Default provider connection** in incident settings. The [workspace MCP guide](CUSTOMIZATION.md#workspace-mcp-servers) covers the incident.io endpoint and authentication configuration. Other integrations, including Datadog and workspace-connected knowledge sources, use the same configured MCP tools as ordinary system threads.
4. Enable Incidents and create a matching public internal channel, or rename an existing channel into the prefix. The bot should join and the incident should appear. Existing channels are not scanned for enrollment. Other bots' channel messages are evidence; Open SWE's own publications are excluded.
5. Use **Ask Open SWE** with the incident.io ID or link to request provider context or actions through the configured MCP connection. There are no separate provider management screens. The provider attachment API remains available for integrations and verifies that the external incident has the same Slack channel.

Provider lifecycle changes require an explicit responder action. Pausing or completing agent activity does not change provider status or severity. If a write has an uncertain outcome, ask the agent to check provider state before retrying. Provider outages leave accepted Slack events in the durable inbox.

## Documents and history

The main list has **Active**, **Inactive**, and **All** filters beside search. Active includes investigations needing attention; Inactive includes paused and completed agent activity. These filters do not change the provider's incident status. Incidents uses the same Open SWE sidebar as Reviews, including projects, recent threads, search, and settings. **Incident history** opens retained summaries and postmortems.

The detail page opens on **Overview**, with the latest finding, suggested next steps, sources, and coverage. **Postmortem** shows the latest agent summary and a **Copy incident** action; **Timeline** shows chronological agent activity.

Slack updates show a concise finding, the first suggested next step when available, and links to sources and the investigation. Detailed impact, checks, hypotheses, and coverage gaps remain on the incident page. Recommendations are separate from completed actions.

The agent stores one Markdown summary per incident in the existing LangGraph Store and replaces it after each investigation. It includes findings, impact, next steps, and evidence links. Responders can paste it into their preferred document or incident tool for editing and publishing.

**Copy incident** copies the visible postmortem as Markdown, including source links. The status-page section is hidden while its workflow is being designed. Provider postmortems remain separately attributed content; provider postmortem writes and status-page publishing remain unsupported by the provider adapter. An authorized responder can ask the agent to implement a fix, open a PR, or perform a specified action through its configured workspace tools. Ordinary channel messages steer investigation; they do not authorize external changes.

Use **Incident history** to search retained incident metadata and postmortems. The agent can also search and read permitted historical incidents as context; a prior cause does not establish the current cause. Channel access is rechecked for history and summary reads. Expired or unavailable evidence is marked unavailable rather than restored from historical references.

## Runtime and retention

The existing `scheduler` graph runs channel-worker and admission jobs; analysis runs on `agent` in a persistent conversation. Incidents adds no graph entrypoints. Provider commands enter the durable receipt queue and are applied by the channel worker.

Accepted events survive dispatch failures, and minute recovery retries pending work. One channel worker is admitted at a time per workspace. New messages are debounced; explicit questions steer a pass. Access and stop controls are checked at tool and publication boundaries. An event Slack never delivers cannot be recovered through the inbox.

Raw incident messages and conversation checkpoints expire after 30 days from enrollment. Curated incident metadata, postmortem summaries, and provider history remain separately stored without automatic expiry. Older stored postmortems remain readable until the next agent update. Receipt and publication cleanup removes old operational content while retaining the identities needed to avoid duplicate work. A channel registration remains to prevent automatic re-enrollment.

An uncertain Slack send is recorded and is not sent again automatically; a responder must inspect the channel. This is separate from provider lifecycle reconciliation. Dashboard access checks do not protect direct LangGraph Store or thread APIs, which require the deployment's own access controls.

## Check a live installation

- Create a matching public channel and verify enrollment, membership, findings, and evidence links.
- Ask while paused and confirm the answer preserves the paused state.
- Ask Open SWE to read an incident.io incident through the configured MCP connection.
- Copy the incident from the Postmortem tab and verify the Markdown and source links paste correctly.

These checks require your own installation; a synthetic pass does not establish live provider compatibility, delivery, or diagnostic quality.
