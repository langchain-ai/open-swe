# Incidents architecture

Incidents builds on the investigation workflow from PR #2497 and the system-owned thread support in PR #2647. It uses the main `agent` graph for reasoning and keeps durable channel scheduling outside the conversation.

## Main agent and scheduling

Slack events and dashboard commands enter a durable receipt inbox. The existing `scheduler` graph runs incident admission and channel-worker jobs. Admission allows one channel worker per workspace; the worker handles debounce, control commands, retries, documents, provider operations, and deduplicated Slack publications. Incidents adds no graph entrypoints.

Each incident has a separate system-owned, public conversation thread running `agent`. Saved thread metadata and a stored pass binding attach incident context and verify access. Caller-supplied source flags cannot grant or bypass that scope. Each pass carries its question, evidence, policy, scope, run identity, and final report. An uncertain dispatch is recovered using the pass ID in run metadata.

The main factory uses the normal system-thread runtime: persistent sandbox, code and PR tools, workspace MCP discovery, browser integration, organization skills, subagents, model selection, and conversation compaction. Repository access uses those existing tools; incident-specific tools only add related-incident lookup. The normal prepare-run, GitHub proxy refresh, PR creation, and workspace authorization paths remain active. PRs use the GitHub App identity; personal integrations and credentials remain unavailable.

Incident instructions are appended to the normal system prompt. Automatic passes investigate and propose mitigation; external actions follow only the current explicit, authorized responder request saved in the pass. A question does not authorize unrelated remediation, and ordinary Slack messages or retrieved material are evidence rather than action instructions. This is agent behavior guidance, not a separate read-only execution sandbox. Configured workspace permissions and the normal tool guards still apply.

Parent and subagent execution recheck incident access. Subagents inherit the current request and incident instructions but do not finalize another incident report. Ordinary tool results retain their normal content and state updates, with evidence IDs attached so actions, returned PR links, and integration observations can be cited. Failed tool results produce coverage gaps. Workspace MCP changes invalidate retained context regardless of which integration supplied it.

Completed tool results are recorded before the post-call access check, even when the pass stops while an action is running. Later passes in the same conversation receive bounded outcome context for reconciliation. This record does not replay tools or guarantee exactly-once external actions; a call interrupted before its result is known still requires checking the external system.

Background-command completions and scheduled wakeups enter the incident receipt queue. Due wakeups are admitted by the coordinator's recovery tick, with the normal debounce and pause rules. Followups supply investigation context and do not renew earlier action authorization. Pending followups are bound to their conversation and access scope; accepted followup context is removed when the conversation resets.

Slack tools receive the destination from the verified incident record. The worker owns automatic report publication and debounce; the agent is instructed not to duplicate those updates with direct Slack calls. Extra communications require a responder request.

Conversations persist across normal passes. Changed or deleted Slack context, changed provider/evidence permissions, and revoked related-incident access create a fresh conversation checkpoint, including fresh offloaded files. Previous findings are omitted from the reset input. Historical pass records allow cleanup to remove every conversation generation.

Controls and access are rechecked before model calls, tool execution, report finalization, and publication. A pause stops automatic analysis but permits explicit questions. Agent completion does not resolve an attached provider incident. Generic dashboard thread listings, detail routes, and cancellation routes do not expose these conversations; use Incidents.

## Incident provider

An administrator creates an existing workspace MCP connection to `https://mcp.incident.io/mcp`. Authentication stays in workspace MCP configuration. Incident settings can preselect a default connection, but attachment is an explicit responder action. A saved binding contains the provider, connection name, external incident ID, and verified Slack association.

The adapter uses the discovered tool schemas for `incident_show`, `incident_list`, `incident_update`, and `resource_show`. It validates arguments, preserves provider status/severity IDs and names, and reads organisation configuration for lifecycle choices. Unsupported schemas and capabilities are reported explicitly. Historical provider results require readable public internal Slack associations and never enroll channels.

Refresh stores the snapshot, successful sync time, and connection scope. Disabled connections, removed read permissions, changed credentials, or revoked channel access prevent stale protected snapshots from being used. Temporary outages may show a previously verified snapshot only under the same authorized scope. Slack receipts remain durable while provider refresh is unavailable.

Provider API lifecycle changes enter the worker's receipt queue. Operations preserve their identity and outcome. An uncertain write is reconciled against provider state without blindly resending it; confirmed state is stored separately from pending operations.

The agent may also call configured incident.io MCP tools directly when a responder requests an action. These calls retain normal MCP authorization and tool results; they do not use the provider adapter's durable operation reconciliation. After an ambiguous result, inspect provider state before retrying.

## Documents and communications

Open SWE owns the working postmortem and a separate status-page draft. The initial postmortem contains summary, impact, timeline, cause, mitigation, resolution, follow-ups, and evidence sections. Subsequent analysis appends dated findings, preserving responder edits. Unknown cause remains unknown.

Every accepted document edit produces an immutable revision with author, source, run, timestamp, and expected prior revision. Human edits and agent updates pass through the channel worker. Conflicting saves preserve the newer text and expose the conflict; retries do not create duplicate revisions.

The status-page section is hidden pending workflow design; existing draft revisions remain stored. Provider postmortems remain separately attributed context for the agent. The verified incident.io MCP contract does not provide explicit status-page publishing or postmortem-write tools, so those capabilities remain unsupported. No general-purpose management tool is used as a publishing fallback.

## History and retention

Curated metadata, document revisions, and provider operation history live outside the expiring operational record. The dashboard searches retained local history, shows revisions, and links to readable incidents. Provider history and lifecycle actions are available through the agent’s configured MCP connection; there are no separate provider management screens. The main agent can search and read related incidents as historical context, with dependency access checked again before later use. Historical causes do not establish the current cause.

Raw messages, recorded tool outcomes, and all conversation checkpoints expire after 30 days from enrollment. Curated history has no automatic expiry and remains subject to current workspace and channel access. Expired or unavailable evidence references are marked unavailable. The channel registration remains so cleanup does not trigger automatic re-enrollment.

## Validation and deployment

Targeted tests exercise durable retries, main-agent assembly/execution, citation validation, scope revocation, provider binding and reconciliation, document conflicts, retention, dashboard authorization, and UI operations. Test fixtures simulate external services; live incident.io compatibility and Slack delivery require a configured installation.

See [installation](INSTALLATION.md#incidents) and [local development](incidents-local.md) for setup. The deployment must protect direct LangGraph thread and Store APIs independently of dashboard access checks.
