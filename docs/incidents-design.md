# Incidents architecture

Incidents builds on the investigation workflow from PR #2497 and the system-owned thread support in PR #2647. It uses the main `agent` graph for reasoning and keeps durable channel scheduling outside the conversation.

## Main agent and scheduling

Slack events and dashboard commands enter a durable receipt inbox. `incidents_coordinator` admits one channel worker per workspace; the `incidents` graph handles debounce, control commands, retries, documents, provider operations, and deduplicated Slack publications. These two graphs are operational schedulers. Neither assembles its own reasoning agent.

Each incident has a separate system-owned, public conversation thread running `agent`. Saved thread metadata and a stored pass binding select incident capabilities. Caller-supplied source flags cannot grant or bypass that scope. Each pass carries its question, evidence, policy, scope, run identity, and final report. An uncertain dispatch is recovered using the pass ID in run metadata.

The main factory shares model selection, settings, middleware, workspace skills, and conversation compaction. Incident sessions use a state-backed filesystem, restricted evidence tools, and no executable sandbox or delegated coding agents. Tool execution is checked as well as the model's visible tool list. Personal integrations and credentials are excluded.

Conversations persist across normal passes. Changed or deleted Slack context, changed provider/evidence permissions, and revoked related-incident access create a fresh conversation checkpoint, including fresh offloaded files. Previous findings are omitted from the reset input. Historical pass records allow cleanup to remove every conversation generation.

Controls and access are rechecked before model calls, tool execution, report finalization, and publication. A pause stops automatic analysis but permits explicit questions. Agent completion does not resolve an attached provider incident. Generic dashboard thread listings, detail routes, and cancellation routes do not expose these conversations; use Incidents.

## Incident provider

An administrator creates an existing workspace MCP connection to `https://mcp.incident.io/mcp`. Authentication stays in workspace MCP configuration. Incident settings can preselect a default connection, but attachment is an explicit responder action. A saved binding contains the provider, connection name, external incident ID, and verified Slack association.

The adapter uses the discovered tool schemas for `incident_show`, `incident_list`, `incident_update`, and `resource_show`. It validates arguments, preserves provider status/severity IDs and names, and reads organisation configuration for lifecycle choices. Unsupported schemas and capabilities are reported explicitly. Historical provider results require readable public internal Slack associations and never enroll channels.

Refresh stores the snapshot, successful sync time, and connection scope. Disabled connections, removed read permissions, changed credentials, or revoked channel access prevent stale protected snapshots from being used. Temporary outages may show a previously verified snapshot only under the same authorized scope. Slack receipts remain durable while provider refresh is unavailable.

Responder lifecycle changes enter the worker's receipt queue. Operations preserve their identity and outcome. An uncertain write is reconciled against provider state without blindly resending it; the dashboard displays confirmed state separately from pending operations.

## Documents and communications

Open SWE owns the working postmortem and a separate status-page draft. The initial postmortem contains summary, impact, timeline, cause, mitigation, resolution, follow-ups, and evidence sections. Subsequent analysis appends dated findings, preserving responder edits. Unknown cause remains unknown.

Every accepted document edit produces an immutable revision with author, source, run, timestamp, and expected prior revision. Human edits and agent updates pass through the channel worker. Conflicting saves preserve the newer text and expose the conflict; retries do not create duplicate revisions.

The status-page draft is editable and copyable. Saving it does not publish externally. Provider postmortems are separately attributed read-only content. The verified incident.io MCP contract does not provide explicit status-page publishing or postmortem-write tools, so those capabilities remain unsupported. No general-purpose management tool is used as a publishing fallback.

## History and retention

Curated metadata, document revisions, and provider operation history live outside the expiring operational record. The dashboard searches retained local history and provider history, shows revisions, and links to readable incidents. The main agent can search and read related incidents as historical context, with dependency access checked again before later use. Historical causes do not establish the current cause.

Raw messages and all conversation checkpoints expire after 30 days from enrollment. Curated history has no automatic expiry and remains subject to current workspace and channel access. Expired or unavailable evidence references are marked unavailable. The channel registration remains so cleanup does not trigger automatic re-enrollment.

## Validation and deployment

Targeted tests exercise durable retries, main-agent assembly/execution, citation validation, scope revocation, provider binding and reconciliation, document conflicts, retention, dashboard authorization, and UI operations. The synthetic local preview exercises the real API and worker/document paths with simulated Slack/model responses. It does not validate live incident.io schemas, credentials, or delivery.

See [installation](INSTALLATION.md#incidents) and [local development](incidents-local.md) for setup. The deployment must protect direct LangGraph thread and Store APIs independently of dashboard access checks.
