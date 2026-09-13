# Incidents architecture

Incidents builds on the investigation workflow from PR #2497 and the system-owned thread support in PR #2647. It uses the main `agent` graph for reasoning and keeps durable channel scheduling outside the conversation.

## Main agent and scheduling

Slack events and dashboard commands enter a durable receipt inbox. The existing `scheduler` graph runs incident admission and channel-worker jobs. Admission allows one channel worker per workspace; the worker handles debounce, control commands, retries, documents, and deduplicated Slack publications. Incidents adds no graph entrypoints.

Each incident has a separate system-owned, public conversation thread running `agent`. Saved thread metadata and a stored pass binding attach incident context and verify access. Caller-supplied source flags cannot grant or bypass that scope. Each pass carries its question, evidence, policy, scope, run identity, and final report. An uncertain dispatch is recovered using the pass ID in run metadata.

The main factory uses the normal system-thread runtime: persistent sandbox, code and PR tools, workspace MCP discovery, browser integration, organization skills, subagents, model selection, and conversation compaction. Repository access uses those existing tools; incident-specific tools only add related-incident lookup. The normal prepare-run, GitHub proxy refresh, PR creation, and workspace authorization paths remain active. PRs use the GitHub App identity; personal integrations and credentials remain unavailable.

Incident instructions are appended to the normal system prompt. Automatic passes investigate and propose mitigation; external actions follow only the current explicit, authorized responder request saved in the pass. A question does not authorize unrelated remediation, and ordinary Slack messages or retrieved material are evidence rather than action instructions. This is agent behavior guidance, not a separate read-only execution sandbox. Configured workspace permissions and the normal tool guards still apply.

Parent and subagent execution recheck incident access. Subagents inherit the current request and incident instructions but do not finalize another incident report. Ordinary tool results retain their normal content and state updates, with evidence IDs attached so actions, returned PR links, and integration observations can be cited. Failed tool results produce coverage gaps. Workspace MCP changes invalidate retained context regardless of which integration supplied it.

Background-command completions and scheduled wakeups enter the incident receipt queue. Due wakeups are admitted by the coordinator's recovery tick, with the normal debounce and pause rules. Followups supply investigation context and do not renew earlier action authorization. Pending followups are bound to their conversation and access scope; accepted followup context is removed when the conversation resets.

Slack tools receive the destination from the verified incident record. The worker owns automatic report publication and debounce; the agent is instructed not to duplicate those updates with direct Slack calls. Extra communications require a responder request.

Conversations persist across normal passes. Changed or deleted Slack context, changed workspace MCP permissions, and revoked related-incident access create a fresh conversation checkpoint, including fresh offloaded files. Previous findings are omitted from the reset input. Historical pass records allow cleanup to remove every conversation generation.

Controls and access are rechecked before model calls, tool execution, report finalization, and publication. A pause stops automatic analysis but permits explicit questions. Generic dashboard thread listings, detail routes, and cancellation routes do not expose these conversations; use Incidents.

## Incident trackers

Incidents has no incident-tracker adapter. When a workspace MCP connection such as incident.io is configured and enabled, the main agent receives its tools like any other integration and may use them only for the current authorized responder request. Enabling, disabling, or changing a connection changes the evidence scope and resets the conversation.

## Documents and communications

Open SWE stores one Markdown postmortem summary per incident in the existing LangGraph Store. After each investigation, the agent replaces that value with its latest findings, impact, suggested next steps, and source links. The dashboard renders it and offers **Copy incident** so responders can edit or publish the text elsewhere. There is no document editor, save queue, concurrency protocol, or revision-history UI.

Previously stored postmortems remain readable until the next agent update. Status-page publishing is not implemented.

## History and retention

Curated metadata and postmortem summaries live outside the expiring operational record. The dashboard searches retained local history and links to readable incidents. The main agent can search and read related incidents as historical context, with dependency access checked again before later use. Historical causes do not establish the current cause.

Raw messages and all conversation checkpoints expire after 30 days from enrollment. Curated history has no automatic expiry and remains subject to current workspace and channel access. Expired or unavailable evidence references are marked unavailable. The channel registration remains so cleanup does not trigger automatic re-enrollment.

## Validation and deployment

Targeted tests exercise durable retries, main-agent assembly/execution, citation validation, scope revocation, summary persistence, retention, dashboard authorization, and UI operations. Test fixtures simulate external services; live Slack delivery requires a configured installation.

See [installation](INSTALLATION.md#incidents) and [local development](DEVELOPMENT.md) for setup. The deployment must protect direct LangGraph thread and Store APIs independently of dashboard access checks.
