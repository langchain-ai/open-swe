# Audit logging

Audit capture is opt-in. Add `@audit_endpoint` below the FastAPI route decorators for an authenticated dashboard mutation, or wrap a tool with `@audit_tool()` outside its access-policy decorator. Unmarked operations are not persisted. New endpoints and tools must make this decision explicitly during review.

The HTTP middleware still creates request-local context before authentication so verified actors and settings-change enrichment can be attached. It persists only marked dashboard write routes with a verified actor, including failed operations. Tools already use an opt-in wrapper; `skip_read=True` keeps mixed read/write settings tools quiet on reads. Neither mechanism retains arguments, payloads, or returned values, with one exception: `expedite_pr_approval` records the hunks the agent excluded from the approval card (requested and resolved, with each guideline and reason), the base and head SHAs, and a hash of the target repository's `.open-swe/APPROVALS.md`, since nobody else reviews those hunks.

## Capture policy

| Surface | Opted in | Left out |
| --- | --- | --- |
| Settings and identity | Profile/preferences, user/repository instructions, review enablement/styles, API-key creation/revocation, Slack bot allowlist, LangSmith disconnection | OAuth handoffs/callbacks, logout (no authenticated actor dependency), onboarding dismissal |
| MCP | Connection changes, managed connection, explicit secret-header reveal | Discovery and CLI invocation of arbitrary external tools |
| Administration | Workspace creation/configuration/deletion/refresh, automation CRUD/trigger, incident settings/commands, organization skills | Listing/status reads, inbound service webhooks |
| Threads | Uploads, lifecycle/run actions, sharing, pinning, commands, terminal access, artifact edits/comments, workflow-push decisions, explicit feedback | History, event streams, batched PR-check reads |
| Reviews and GitHub | PR actions, review requests/assignment, comments and pending-review edits, submission/discard, labels, re-review/scout, review-style analysis/control | Summary queries, viewed telemetry, chat history/streams |
| Bridges | Open and close | Heartbeats, claims and request replies (transport/liveness traffic) |
| Analytics | None | Page views and client-error reports; Segment analytics remains unchanged |
| Agent tools | User settings/instructions/skills, shared flags/approval mode, workspace/automation administration, PR opening/linking/readiness/approval/review publication, human/expedited review actions, thread lifecycle, sandbox recreation, thread feedback | Reads/search, sandbox file/command operations, previews/downloads, polling/watches, wakeups/subscriptions, Slack delivery, internal finding/walkthrough bookkeeping, assessment ratings and draft review proposals |

Tool audit wrappers also apply when the same tool is invoked through the sandbox tool bridge or MCP exposure; the generic transport itself is not audited. Internal helpers are not separately wrapped, avoiding an audit entry for each implementation step. Arbitrary third-party MCP tools are not covered by this application audit policy.
