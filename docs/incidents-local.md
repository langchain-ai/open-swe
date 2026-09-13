# Incidents locally

Incidents lives at `/incidents`. It enrolls public internal Slack channels after a new `channel_created` or `channel_rename` event matches the configured prefix. Each incident keeps a persistent, system-owned conversation on the main `agent` graph, using the normal sandbox, coding tools, subagents, and workspace integrations. The dashboard separates agent activity, provider lifecycle, and public communication drafts.

## Start the dashboard

From the repository root, run:

```sh
make dev-ui
```

Open [http://localhost:2024/incidents](http://localhost:2024/incidents) and sign in. Vite serves assets on port 3000; the backend and dashboard run on 2024. Responders need an email in `OBSERVABILITY_AUTHORIZED_EMAILS` or admin access through `CONFIGURED_ADMINS`. Incident settings require an administrator.

For a populated local preview, leave `make dev-ui` running and use another terminal:

```sh
.venv/bin/python -m tests.incidents.preview
```

Open [http://127.0.0.1:2025/incidents](http://127.0.0.1:2025/incidents). A yellow banner identifies synthetic data. This test-only process runs the real incident API, durable receipt handling, Store serialization, coordinator, worker, and document operations with in-memory state and simulated Slack/model responses. It blocks external network calls and does not load environment files. Its local session and state belong only to the preview process; restarting clears the data.

Try asking a question, pausing, resuming, completing, and reopening the checkout incident. Edit the postmortem, copy it as Markdown, then inspect its revision history. Explicit questions can run while paused or completed and preserve that activity state. The preview does not exercise a live model conversation, provider connection, or Slack delivery.

## Connect a Slack workspace and provider

1. Follow [Incidents setup](INSTALLATION.md#incidents), including the Slack manifest, `SLACK_APP_ID`, and responder access. Deliver events to the signed `/webhooks/slack` endpoint. For local development, use the [webhooks-only tunnel](DEVELOPMENT.md#3-tunnel-for-webhooks); keep the raw LangGraph APIs private.
2. In **Admin → Incidents**, set a literal channel prefix such as `inc-`. The installed bot supplies the workspace identity, and `SLACK_APP_ID` supplies the app identity. Optionally choose a supported model and adjust the analysis limits.
3. For incident.io, configure an existing connection under **Admin → Workspace MCPs**, then enter its name as **Default provider connection** in incident settings. The [workspace MCP guide](CUSTOMIZATION.md#workspace-mcp-servers) covers the incident.io endpoint and authentication configuration. Other integrations, including Datadog and workspace-connected knowledge sources, use the same configured MCP tools as ordinary system threads.
4. Enable Incidents and create a matching public internal channel, or rename an existing channel into the prefix. The bot should join and the incident should appear. Existing channels are not scanned for enrollment. Other bots' channel messages are evidence; Open SWE's own publications are excluded.
5. Attach an incident.io incident from the detail page using its ID or link. Attachment requires the provider to report the same Slack channel. The provider panel shows status, severity, last sync, available actions, and errors. Historical provider search can read older incidents without enrolling their channels.

Provider lifecycle changes require an explicit responder action. Pausing or completing agent activity does not change provider status or severity. If a write has an uncertain outcome, refresh the provider to reconcile its state before retrying. Provider outages leave accepted Slack events in the durable inbox.

## Documents and history

The main list has **Active**, **Inactive**, and **All** filters beside search. Active includes investigations needing attention; Inactive includes paused and completed agent activity. These filters do not change the provider's incident status. App navigation stays in the sidebar, and **Incident history** opens retained summaries and postmortems.

The detail page opens on **Overview**, with the latest finding, evidence-backed suggested next steps, sources, and provider context. **Postmortem** contains the editable incident document, a **Copy incident** action, and revision history; **Timeline** shows chronological agent activity. Switching tabs preserves unsaved document edits. Temporary Slack verification failures preserve drafts; revoked channel access hides incident content.

Slack updates show a concise finding, the first suggested next step when available, and links to sources and the investigation. Detailed impact, checks, hypotheses, and coverage gaps remain on the incident page. Recommendations are separate from completed actions.

The postmortem starts with summary, impact, timeline, cause, mitigation, resolution, follow-ups, and evidence sections. Later reports append dated findings, preserving human edits. Each accepted change creates an immutable revision with author, time, source, and the expected prior revision. A conflicting save preserves the newer text and asks the responder to reload.

**Copy incident** copies the visible postmortem as Markdown, including source links and any unsaved edits. The status-page section is hidden while its workflow is being designed. Provider postmortems remain separately attributed content; provider postmortem writes and status-page publishing remain unsupported by the provider adapter. An authorized responder can ask the agent to implement a fix, open a PR, or perform a specified action through its configured workspace tools. Ordinary channel messages steer investigation; they do not authorize external changes.

Use **Incident history** to search retained incident metadata and postmortems. The agent can also search and read permitted historical incidents as context; a prior cause does not establish the current cause. Channel access is rechecked for history and revision reads. Expired or unavailable evidence is marked unavailable rather than restored from historical references.

## Runtime and retention

`langgraph.json` registers `incidents` for the per-channel worker and `incidents_coordinator` for admission and recovery. These graphs schedule work; analysis runs on `agent` in a separate persistent conversation. Human document edits and provider commands enter the durable receipt queue and are applied by the channel worker.

Accepted events survive dispatch failures, and minute recovery retries pending work. One channel worker is admitted at a time per workspace. New messages are debounced; explicit questions steer a pass. Access and stop controls are checked at tool and publication boundaries. An event Slack never delivers cannot be recovered through the inbox.

Raw incident messages and conversation checkpoints expire after 30 days from enrollment. Curated incident metadata, document revisions, and provider history remain separately stored without automatic expiry. Authorized responders can still edit retained postmortems and drafts. Receipt and publication cleanup removes old operational content while retaining the identities needed to avoid duplicate work. A channel registration remains to prevent automatic re-enrollment.

An uncertain Slack send is recorded and is not sent again automatically; a responder must inspect the channel. This is separate from provider lifecycle reconciliation. Dashboard access checks do not protect direct LangGraph Store or thread APIs, which require the deployment's own access controls.

## Check a live installation

- Create a matching public channel and verify enrollment, membership, findings, and evidence links.
- Ask while paused and confirm the answer preserves the paused state.
- Attach a provider incident, refresh it, and verify the channel association and lifecycle options.
- Edit a postmortem from two sessions and verify a stale revision produces a conflict.
- Copy the incident from the Postmortem tab and verify the Markdown and source links paste correctly.

The local preview verifies simulated workflows. These checks still require your own installation; a synthetic pass does not establish live provider compatibility, delivery, or diagnostic quality.
