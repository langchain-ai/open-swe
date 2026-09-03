# OEP-0001: Thread identity and credential boundaries

- **Authors:** Mukil Loganathan (`@langchain-infra`), Ramon Nogueira (`@ramon-langchain`)
- **Status:** Draft
- **Created:** 2026-09-03
- **Discussion:** To be added when the proposal PR is opened
- **Supersedes:** None

## Summary

Collaborative threads use a shared Open SWE bot identity and a least-privilege set of team
credentials. A user who needs personal OAuth or credentials moves the work into a personal thread
with a single credential principal. Personal threads are private by default and may be shared only
without transferring authority to another participant.

## Motivation

Identity becomes ambiguous when several people participate in a thread but the agent acts with one
participant's credentials. A later participant could otherwise create a pull request, query a
service, or mutate a resource under somebody else's identity. Participants may also incorrectly
assume that their own permissions govern an action.

The permission model should be explainable from the thread type, enforce least privilege, preserve
useful collaboration, and make the actor shown by downstream systems truthful.

## Proposal

Every thread has one credential principal for its lifetime:

1. **Collaborative thread:** The principal is the Open SWE bot. The thread may have multiple
   participants. It can use only explicitly configured team credentials intended for shared use,
   scoped to a least-privilege common baseline such as read-only observability access. Actions are
   attributed to the bot, with initiating-user context recorded separately where a downstream
   integration supports it.
2. **Personal thread:** The principal is one user. The thread may use that user's OAuth grants and
   personal credentials. Creating or escalating to this thread is an explicit boundary crossing,
   not an in-place credential upgrade of a collaborative thread. It is private by default.

A personal thread may be shared for visibility, but sharing does not let viewers invoke actions
with the owner's credentials. Interactive collaboration that permits multiple participants to
instruct the agent requires returning to bot identity or creating a new collaborative thread. A
completely private, owner-only thread remains available.

Credential selection is determined by thread principal, not by whichever participant sent the
latest message. A thread cannot silently switch principals, combine users' credentials, or fall
back from failed personal authorization to another person's credentials. The product must show the
active principal and authorization boundary before credentialed actions.

Escalation carries only the context the user explicitly chooses into a new personal thread. It does
not expose personal credentials to the source thread, other participants, the model transcript, or
the sandbox filesystem. Implementations should keep secrets server-side or behind credential
proxies and issue narrowly scoped, short-lived access where possible.

### Non-goals

- Defining the exact UI for escalation, sharing, or principal indicators.
- Selecting the initial list of shared team integrations and permissions.
- Replacing integration-specific approval, audit, or authorization controls.
- Allowing a collaborative thread to impersonate a participant for convenience.

## Security and privacy

This model makes the thread the stable authorization boundary and prevents confused-deputy actions
under the credentials of an arbitrary participant. Shared bot permissions must remain intentionally
limited because every authorized participant in a collaborative thread can potentially cause their
use.

Personal-thread visibility and action authority are separate. If a personal thread is shared, its
transcript and outputs may reveal sensitive information returned by personal integrations even when
viewers cannot take actions. Sharing therefore requires an explicit owner action and a clear warning
about existing content. Deployments may forbid sharing when an integration's data policy requires
owner-only visibility.

Audit records should capture the thread principal, requesting participant, credential source,
integration, action, and result without recording secret material.

## Alternatives

### Use the latest participant's credentials

This makes authorization change message by message, surprises collaborators, and can cause work to
be attributed to the wrong person.

### Let collaborative threads use the creator's credentials

This is simple initially but grants every later participant indirect use of the creator's authority
and leaves stale access after the creator departs.

### Give the bot broad organization-wide permissions

A single identity is understandable, but broad access expands the blast radius of mistakes and
compromise. The shared baseline should instead be intentionally narrow, with personal authority
used only across an explicit boundary.

### Keep personal threads public by default

Public visibility supports open collaboration, but personal integrations can return private data
before the owner notices. Private-by-default is the safer default; explicit sharing preserves the
collaboration use case.

## Unresolved questions

- Should shared personal threads be read-only for everyone except the owner, or can selected users
  send non-credentialed messages while credentialed actions remain owner-approved?
- Which transcript and derived artifacts are copied when escalating from collaborative to personal?
- What product term best distinguishes personal credential authority from transcript visibility?
- Which shared bot capabilities form the default baseline, and who administers them?

## Resolution

Pending.
