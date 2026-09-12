# Incident system-thread runtime

**Goal:** Give incident conversations the normal system-owned agent runtime and workspace capabilities, retaining incident context, access checks, durable reports, and Slack orchestration.

**Approved design:** The September 11 conversation authorizes the main system-thread toolset, persistent sandbox, workspace MCPs, browser integration, and subagents. Automatic passes investigate and propose mitigation. Only a directed authorized responder request supplies instructions for external actions; channel messages and retrieved material remain evidence. System ownership never borrows a responder's personal integrations or PR identity.

**Constraints:** No new dependencies. Preserve normal tool guards, incident pause/revocation checks, report citations, debounce, and publication deduplication. No production remediation or external test messages during implementation. Use targeted tests only. This change does not add operational memory or new provider APIs.

## Implementation

- [x] Extend `tests/agent/test_agent_incidents.py` to exercise incident runs through the normal factory: sandbox, PR tools/guard, workspace MCP discovery, browser tools, skills, subagents, and exclusion of personal credentials/tools. Retain ordinary factory regression coverage.
- [x] Update `tests/incidents/test_runtime.py` to verify prompt composition, full tool execution, generic evidence capture (including failed tool results and state-changing tool commands), MCP revocation, and subagent scope checks without publishing a second report.
- [x] In `agent/server.py`, remove the incident-only runtime substitutions. Add incident tools to normal tools; install normal prepare/PR/runtime middleware and layer incident checks onto parent and subagent execution. Bind Slack tools only to the verified saved incident channel.
- [x] In `agent/incidents/runtime.py`, compose incident instructions with normal system instructions, include the saved authorized request, remove the tool allowlist and duplicate MCP loader, and attach evidence references to ordinary tool results without changing their state updates. Track all enabled workspace MCP connections for scope invalidation.
- [x] Preserve the structured report contract and current worker/document pipeline. Update the obsolete read-only language in `agent/incidents/engine.py` and incident documentation.
- [x] Record bounded completed-tool outcomes before post-call checks so stopped passes can reconcile results on retry; retain conversation scope and delete outcomes at incident expiry.
- [x] Route background completions and scheduled wakeups through the incident inbox, preserving due times, pause behavior, scope boundaries, and context-only authorization. Drop accepted followups when rotating conversation scope.
- [x] Run targeted factory, runtime, worker/coordinator, credential-scope, PR, background/wakeup, dashboard, and UI tests; scoped Ruff/ty and UI checks. Review the final diff for accidental tool/credential expansion beyond ordinary system threads.

## Verification

Final targeted Python run: **532 passed, 3 skipped**. UI incidents/document/manifest tests: **31 passed**. Scoped Ruff lint/format, Python type checking, UI type checking and lint, and `git diff --check` passed. Independent code review findings were addressed and verified. The PR action path uses a simulated tool through the compiled main graph; no live PR, incident.io mutation, or Slack message was sent.

The local backend, UI server, and ngrok tunnel are stopped. This implementation pass did not restart them.

## Workspace recovery

The earlier `/private/tmp/open-swe-incidents` checkout was absent when work resumed. Restored `codex/incidents` at `3582c695` into `.worktrees/incidents`, then replayed the saved source patches for OAuth, Slack formatting/session status, channel replies/debounce, and Markdown documents. Recovery baseline: 121 runtime/worker/coordinator/service tests pass. No archived Slack/network operations were replayed.
