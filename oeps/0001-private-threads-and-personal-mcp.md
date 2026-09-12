# OEP-0001: Private threads and personal MCP access

- **Authors:** Ramon Nogueira (`@ramon-langchain`), Mukil Loganathan (`@langchain-infra`), Johannes Jolkkonen (`@johannes117`)
- **Status:** Draft
- **Created:** 2026-09-08
- **Discussion:** https://github.com/langchain-ai/open-swe/pull/2505
- **Supersedes:** None

## Summary

Open SWE should make thread privacy the boundary for personal credentials. Every thread is either
collaborative or private for its entire lifetime. Collaborative threads may be prompted by multiple
users and run only as a shared, least-privilege Open SWE identity. Private threads have one immutable
owner, are promptable only by that owner, and may use the owner's MCP connections and OAuth grants.
Workspace administrators may view private threads but cannot prompt them or invoke the owner's tools.

Slack channel threads are always collaborative and Slack bot DM threads are always private. Dashboard
threads default to private, with a per-user setting for the creation default. When work in a
collaborative thread needs personal authority, the user starts a private continuation that copies the
transcript as marked, untrusted collaborative-origin context while dropping Slack and issue linkage.
No personal tool becomes available until the owner sends the first message in the continuation.
Private threads cannot become collaborative in v1; read-only publication may be added later.

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

Every thread is created as one of two immutable kinds:

1. **Collaborative thread:** Multiple authorized users may view and prompt it. Its credential
   principal is the Open SWE service identity. It may use only administrator-managed capabilities
   explicitly approved for every potential participant, such as read-only Datadog or narrowly scoped
   GCP access. The baseline must be least privilege and centrally auditable. Automations and other
   system-initiated work use this kind and identity by default.
2. **Private thread:** Exactly one immutable user owns and may prompt it. Its credential principal is
   that owner. It may load and invoke the owner's MCP connections and personal OAuth grants. It is
   visible only to the owner and administrators. If the owner is an administrator, the private thread
   receives the admin capabilities permitted by the deployment; no separate admin-thread type exists.

Administrators retain view access for support, security, and governance, but cannot prompt another
user's private thread, approve actions, resume its agent, or invoke its tools. Administrative viewing
must be explicit and audited. The product must show the thread kind and active principal before
credentialed actions.

### Personal MCP discovery and invocation

A collaborative thread must not receive personal MCP credentials, tool handles, tool schemas,
sensitive configuration, integration data, OAuth grants, or the ability to load or invoke personal
servers. It may expose enough non-sensitive capability metadata to explain that a private
continuation can perform an otherwise unavailable action without weakening the boundary.

A private thread resolves personal MCP and OAuth access from its fixed owner, not from the latest
message sender. It cannot combine credentials from multiple users, silently change principal, or
fall back to another person's credentials when authorization fails.

### Starting a private continuation

The owner or agent acting at the owner's request may start a private continuation as a new thread.
The continuation has independent state, sandbox, credentials, tool state, and authorization. It may
copy the source transcript into model context only when every copied item is visibly marked as
collaborative-origin, untrusted context rather than direct user authorization. It must drop Slack,
issue, and other source-thread delivery linkage so private output cannot flow back to the
collaborative surface.

Personal MCPs, OAuth grants, admin capabilities, and other private-thread-only tools remain
unavailable until the owner sends the first message in the continuation and confirms the requested
task. The source thread remains untouched, collaborative, and bound to the Open SWE identity; no
participant is removed and Slack remains consistent.

### Creation, visibility, and prompting

Visibility is selected at creation and never narrows or changes kind in place. Slack channel threads
are always collaborative. Slack bot DM threads are always private. Dashboard-created threads default
to private, and users may configure their own creation default.

A private thread has no collaborative transition in v1. A future read-only publish state may let the
owner expose a transcript without allowing viewers to send messages, resume the agent, approve
actions, or invoke tools. That state requires an explicit warning that outputs may contain data
returned by personal integrations and may be disabled for integrations or data classes requiring
owner-only visibility.

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
can use personal credentials cannot accept prompts from other users. Authorization must be enforced
server-side against the authenticated session and fixed thread owner rather than client-provided
metadata.

Personal MCP secrets must remain encrypted at rest and resolved through existing credential and
tool-loading controls. Thread state should contain opaque credential references, not secret values.
Secrets must not enter model context, new-thread prompts, caches, logs, or sandbox files. Shared Open
SWE permissions must remain intentionally limited because every collaborative participant can
potentially cause their use.

Audit records should capture the thread kind, credential principal, requesting participant,
credential source, integration, action, administrative access, creation source, continuation source,
and result without recording secret material.

Immutable thread kinds remove only the need to poll a thread's own kind and to handle in-place
visibility transitions. They do not remove revocation checks on the authority used inside a thread.
An owner can disconnect an MCP server, revoke an OAuth grant, lose admin privileges, or be disabled
while the thread and its sandbox stay alive. Ownership, grants, and capabilities must therefore be
revalidated whenever credentials are resolved and again when a personal tool is invoked, so revoked
authority stops working immediately rather than persisting for the life of the thread.

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

### Change visibility in place

Narrowing a collaborative thread would strand or eject participants, conflict with Slack channel
visibility, and leave already-disclosed content outside Open SWE's control. Widening a private thread
could disclose personal-integration output. Immutable thread kinds avoid both transitions.

### Make private threads collaborative by default

Personal integrations can return private data before the owner notices. Private dashboard and Slack
DM defaults preserve the owner boundary, while Slack channels remain collaborative by construction.

### Disable personal integrations entirely

This avoids ambiguity but prevents legitimate work requiring user-scoped services. Starting a
private continuation provides that capability behind an explicit boundary.

### Start a private thread without source context

A context-free handoff has the cleanest isolation but makes the user and agent reconstruct the task.
Copying only a marked, untrusted transcript preserves useful context without carrying execution
state, credentials, tool state, source linkage, or authorization across the boundary.

## Resolution

Pending.
