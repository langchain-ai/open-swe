# OEP-0001: Private threads and personal MCP access

- **Authors:** Ramon Nogueira (`@ramon-langchain`), Mukil Loganathan (`@langchain-infra`)
- **Status:** Draft
- **Created:** 2026-09-08
- **Discussion:** https://github.com/langchain-ai/open-swe/pulls?q=is%3Apr+OEP-0001
- **Supersedes:** None

## Summary

Open SWE should make thread privacy the boundary for personal credentials. Collaborative threads
may be prompted by multiple users and run only as a shared, least-privilege Open SWE identity.
Private threads have one owner, are promptable only by that owner, and may use the owner's MCP
connections. On Slack, private threads run as bot DM threads with that user, and every bot DM thread
is private. An admin thread is not a separate concept: it is a private thread owned by an admin, so
private threads created by admins are admin threads by default. When work needs personal authority,
the agent may start a new private DM thread with an agent-supplied prompt; it does not fork or copy
the source thread's state.

A private thread may later be made publicly viewable, but viewers cannot prompt it or invoke the
owner's tools. There is no mode in which personal MCP connections are available to a thread that
other users can prompt.

## Motivation

Identity becomes ambiguous when several people can instruct an agent that acts with one
participant's credentials. A later participant could otherwise query, mutate, or create resources
under somebody else's identity. Making tool access depend on the latest participant or thread
creator would also make downstream attribution misleading and leave stale authority when
participation changes.

Open SWE needs a model that preserves multiplayer collaboration, supports personal integrations
when necessary, and makes the authorization boundary easy to explain and enforce. Privacy and
credential authority should be properties of the thread rather than inferred per message.

## Proposal

Every thread is one of two kinds for its lifetime:

1. **Collaborative thread:** Multiple authorized users may view and prompt it. Its credential
   principal is the Open SWE service identity. It may use only administrator-managed capabilities
   explicitly approved for all participants, such as read-only Datadog or GCP access. The baseline
   must be least privilege and centrally auditable.
2. **Private thread:** Exactly one user owns and may prompt it. Its credential principal is that
   owner. It may load and invoke the owner's MCP connections and personal OAuth grants. It is visible
   only to the owner and administrators by default. If the owner is an administrator, the private
   thread is an admin thread and receives the admin capabilities permitted by the deployment; no
   separate admin-thread type exists.

Administrators retain access for support, security, and governance, but administrative access to
another user's thread must be explicit and audited. The product must show the thread kind, active
principal, and whether its owner grants it admin capabilities before credentialed actions.

### Personal MCP discovery and invocation

In a collaborative thread, the per-user context that already flows into normal threads includes only
the list of MCP servers attached to that user. It must not include credentials, tool handles, tool
schemas, sensitive configuration, integration data, or the ability to load or invoke those servers.
This lets the agent explain that a new private thread can perform an otherwise unavailable action
without weakening the boundary.

A private thread resolves personal MCP access from its fixed owner, not from the latest message
sender. It cannot combine credentials from multiple users, silently change principal, or fall back
to another person's credentials when authorization fails.

### Starting a private thread

The owner or agent acting at the owner's request may start a new private thread with an
agent-supplied initial prompt. The implementation should enhance the existing new-thread mechanism
rather than introduce thread forking. The new thread has independent state, sandbox, model context,
and cache; no source transcript, execution state, credentials, tool state, or authorization is
copied implicitly.

The initial prompt must be visibly marked as agent-supplied content originating in a non-private
thread and treated as untrusted context rather than direct user authorization. It must instruct the
new agent to confirm the requested task directly with the owner before loading or invoking any
personal MCP, admin, or other private-thread-only tool. Until that confirmation arrives from the
owner in the private thread, those tools remain unavailable. The source thread remains collaborative
and retains its service identity.

### Visibility and prompting

A private thread may be made publicly viewable by an explicit owner action. Public visibility is
read-only: it does not let viewers send messages, resume the agent, approve actions, or invoke tools.
If interactive multiplayer work is needed, the user must create or return to a collaborative thread
that uses the Open SWE identity.

Sharing requires a warning that the existing transcript and outputs may contain data returned by
personal integrations. Deployments may disable sharing for integrations or data classes that require
owner-only visibility.

Slack cannot provide a private thread inside a shared channel because channel members can read it.
Slack private threads therefore use the user's bot DM. Starting one from a channel posts no private
content back to the channel; at most it may leave a non-sensitive indication that work continued
privately. Links and access checks must not make the DM or dashboard thread readable by other Slack
users.

### Shared service identity

The Open SWE identity should receive a small baseline of organization services that are acceptable
for every collaborative-thread participant. Examples include read-only observability access in
Datadog and narrowly scoped GCP access. Administrators own the allowlist and scopes. Adding a
service or permission requires explicit review; broad organization-wide access and personal
impersonation are excluded.

Actions should be attributed to the service identity, with the requesting participant recorded
separately where an integration supports it. Shared credentials remain server-side and must not be
exposed to transcripts or sandbox files.

### Non-goals

- Defining the initial shared-service allowlist or exact Datadog and GCP scopes.
- Solving cost attribution for collaborative threads. Cost accounting is orthogonal to credential
  authority and should be decided separately.
- Replacing integration-specific approval, audit, or authorization controls.
- Allowing collaborative threads to impersonate a participant for convenience.

## Security and privacy

This model makes the ability to prompt a thread the decisive authorization boundary. A thread that
can use personal credentials cannot accept prompts from other users, including after it becomes
publicly viewable. Authorization must be enforced server-side against the authenticated session and
fixed thread owner rather than client-provided metadata.

Personal MCP secrets must remain encrypted at rest and resolved through existing credential and
tool-loading controls. Thread state should contain opaque credential references, not secret values.
Secrets must not enter model context, new-thread prompts, caches, logs, or sandbox files. Shared Open
SWE permissions must remain intentionally limited because every collaborative participant can
potentially cause their use.

Audit records should capture the thread kind, credential principal, requesting participant,
credential source, integration, action, administrative access, thread creation or visibility change, and result
without recording secret material. Public-sharing transitions require explicit confirmation and
must not expose a private thread through guessable identifiers or unauthenticated links.

## Alternatives

### Select credentials from the latest participant

This changes authorization message by message, surprises collaborators, and can attribute work to
the wrong person.

### Let collaborative threads use the creator's credentials

This grants every later participant indirect use of the creator's authority and leaves stale access
when the creator departs.

### Permit personal MCP with per-action owner approval

Approval reduces accidental use but does not remove the confused-deputy risk or private-data leakage
from tool results. A single-owner prompting boundary is simpler and safer.

### Give the Open SWE identity broad organization-wide permissions

A shared identity is understandable, but broad access expands the blast radius of mistakes and
compromise. The shared baseline should instead contain only capabilities acceptable for every
participant.

### Make private threads public or collaborative by default

Personal integrations can return private data before the owner notices. Private-by-default with an
explicit, view-only publication option preserves transparency without transferring authority.

### Disable personal integrations entirely

This avoids ambiguity but prevents legitimate work requiring user-scoped services. Starting a new
private thread provides that capability behind an explicit boundary.

### Fork the collaborative thread into a private thread

Forking could unintentionally carry transcript data, execution state, caches, or authority across
the privacy boundary. A fresh private thread with a marked agent-supplied prompt and direct user
confirmation is easier to reason about and audit.

## Resolution

Pending.
