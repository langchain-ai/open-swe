# Merge queue runtime

The runtime supports native GitHub queue observation and the earlier custom candidate/smoke-validation mode.
Native observation never builds commits, dispatches tests, posts checks, or merges PRs.
Native validation and shared-result publication are separate integration steps.
A custom smoke pass is not permission to merge either.

## Native observation

Configure the repository binding's `merge_queue_config` with:

```json
{
  "mode": "native",
  "repository": "example/queue-canary",
  "repository_id": 123,
  "operator_emails": ["operator@example.com"]
}
```

The existing App needs only Contents, Metadata, and Pull requests read for this observer.
No dispatch URL, signing secret, workflow digest, or check-write access is used.
The repository must already have an integration binding.
Configure the App's `merge_group` subscription in addition to PR/review/push events.
Signed webhooks schedule coalesced durable wakeups; the recurring minute job recovers missed deliveries.
Event payloads never supply candidate state: every wakeup reads the current GitHub queue through the configured App.

The observer atomically replaces `merge_queue.native_snapshot`, including an observation timestamp, target SHA, queue entries, PR heads/review decisions, and candidate parents.
Membership is reconstructed from a continuous chain of merge commits rooted at the observed target, with each second parent matching its PR head.
`baseCommit` can be a preceding candidate, not main.
Inconsistent prefixes remain observations but produce no usable successor candidate until reconciled.
Candidates are keyed by SHA; an empty queue removes them from the current view.
Transport, permission, and incomplete-response errors preserve the last snapshot and its timestamp for the next reconciliation; a snapshot is never test evidence.
Observation currently supports merge-commit queues and at most 100 entries; larger queues are rejected rather than silently truncated.
Queue configuration is not read: GitHub restricts that field separately from entries.
Before enabling validation, verify merge method, limits, and deadlines through an authorized operator or backend relay.

Before switching modes, stop **all** previous custom controllers, including any external owner, and drain their work.
The persisted transition requires a paused queue without a live lease, active requests/attempts/batches, or uncleaned custom refs.
Switching back to custom mode is rejected.
Updated custom workers also reject native queues; this does not fence an older binary that predates the mode field, so stopping those replicas is required.
Observation continues while paused.
The old dashboard controls return 409 in native mode; use GitHub for queue controls until the native UI integration is available.

Before enabling native validation, independently verify enqueue/dequeue and trusted check-reporting authority under the intended App or relay.
Read access and webhook receipt do not prove write permissions.
The eventual gate issuer must be isolated from general agent credentials and candidate workflow code.

## Configuration

The following settings describe **custom mode** (`mode` defaults to `custom`).

Configuration lives in PostgreSQL, in the nullable `integration_repository.merge_queue_config` JSONB column.
Bindings default to NULL, which disables both the API and controller.
An operator can configure one canary repository binding with this object; its routing ID comes from the row:

```json
{
  "repository": "example/queue-canary",
  "repository_id": 123,
  "dispatch_url": "https://dispatcher.example/api/v1/merge-queue/dispatch",
  "workflow_digest": "<sha256 of the reviewed merge_queue_validate.yml bytes>",
  "operator_emails": ["operator@example.com"],
  "policy": {
    "checks": {
      "required-check-name": {
        "app_id": 101,
        "workflow_path": ".github/workflows/check.yml"
      }
    },
    "max_batch_size": 1,
    "collection_seconds": 900,
    "validation": {"dispatch_actor_id": 202}
  },
  "retention_days": 90
}
```

These are illustrative identifiers.
Use the repository's numeric GitHub ID, the actual required-check sources, and the gateway's GitHub App bot **user ID** for `dispatch_actor_id`.
The repository must already have an integration binding.
Startup refuses multiple configured canary repositories.
Apply the additive migration before deploying this runtime; set the column back to SQL NULL to disable it.
Configuration is read at startup, so restart all backend replicas after changes.
Only operators with database access can edit it; the routing API and agent tools do not expose this field.
Jobs and HTTP callers cannot supply policy, GitHub credentials, or target repositories.

Repository unbinding is refused while its binding has queue configuration or any queue row references it, including paused queues and retained history.
Disable queue processing while retaining the repository binding; clearing configuration alone does not permit unbinding a retained queue.
Queue ownership changes require an explicit operator handoff, not unbind/rebind: a new binding never automatically adopts retained queues, evidence, or leases.

The existing Robin App supplies repository-scoped tokens with Contents write and Actions, Checks, Metadata, Pull requests and Statuses read.
It needs no Actions write or queue-specific branch-rule bypass.
Install the reviewed workflow on the canary's `main`; the controller verifies its digest before GitHub reconciliation.
Expired database deadlines still reconcile during a GitHub outage.

Workflow dispatch uses a separately deployed gateway.
Set the operator-managed HTTPS `dispatch_url` and store `MERGE_QUEUE_DISPATCH_SECRET` (at least 32 random characters) only in the backend and gateway secret stores.
Never pass this secret to agent sandboxes or general-purpose agent tools.
Local agent shells are disabled wherever it is present.
The gateway owns its GitHub credentials and must reject ordinary agent credentials.

The gateway contract is a POST containing `contract` (the version-1 validation request) and `expires_at` (the persisted attempt deadline).
The `X-Merge-Queue-Signature` header is the lowercase hex HMAC-SHA256 of the exact UTF-8 body with the shared secret.
Redirects are never followed.
The gateway must verify the signature, expiry, configured repository and numeric ID, exact candidate ref/SHA, current main/workflow revision, reviewed workflow digest and reserved `MERGE_QUEUE_DISPATCH_ACTOR_ID` variable.
The workflow actor is the gateway's bot, not the Robin App.
Dispatch only `merge_queue_validate.yml` on `main` with the unchanged `contract` input.

Before sending to GitHub, the gateway commits a durable receipt keyed by attempt ID and bound to the request body.
Duplicate requests never dispatch again, even following restart or an uncertain GitHub response.
It returns 204 for accepted or already-recorded requests and 4xx for rejected inputs.
A failure before entering the receipt transaction returns 503 with `X-Merge-Queue-Retry-Signature`: the lowercase hex HMAC-SHA256 of `b"retry:" + body` with the shared secret.
Only that authenticated response permits retrying the exact request, up to three deliveries with one second between them, within the worker's existing timeout.
Three such failures reject the attempt immediately.
Receipt/dispatch failures, unsigned 5xx responses and transport errors remain uncertain and go to run discovery.
An accepted request is not successful validation: Robin independently verifies GitHub runs, jobs and artifacts.
Failed delivery can consume an attempt without starting a run; discovery and the saved deadline handle that case.

## Human controls

### Automatic diagnosis and recovery

Native validation can opt into a continuing Robin investigation by adding `recovery` to the repository binding's existing `merge_queue_config`:

```json
{
  "recovery": {
    "channel_id": "<recovery-slack-channel-id>",
    "operator_id": "<linked Robin users.id UUID>"
  }
}
```

Invite Robin to the configured recovery-notification channel before enabling this setting.
The channel must allow internal operations, have Robin as a member, and be readable with the existing Slack integration.
Choose a linked GitHub/Slack user whose verified email is in `operator_emails` and who can access the queue repository.
Apply the additive pause-ownership migration before deploying.
Omitting `recovery` preserves existing configuration hashes and disables automatic diagnosis.

A failed validation binds one durable MDA/Slack thread to its sequence.
The dashboard investigation is private to its configured operator; outcome notices are posted to the configured Slack channel.
The minute reconciler recovers missed job deliveries; keyed jobs deliver changes and scheduled investigations to the same owner.
The `merge-queue-recovery` skill uses read tools for diagnosis and guarded tools for full retry, exact-PR deferral, outage pause/resume, and rare escalation.
The dispatch gateway owns GitHub writes: workflow dispatch, passing-check publication, and native queue removal.
Robin's controller retains diagnosis, pause/resume state, dependency checks, and outcome reconciliation; its native-queue GitHub token is read-only.

The gateway policy `allow_main_advance` (default false; enable with a new policy revision) permits successful tests to approve a replacement candidate after main advances, only for the same ordered queue entries and PR heads under unchanged validation policy.
The check records both tested and accepted SHAs; failed old runs retry on current main within the original batch budget, while changed membership requires fresh testing.

Deferral sends a `POST` to the sibling `deferrals` endpoint of the configured `dispatch_url`.
The request includes the exact original dispatch body, recovery request key, queue entry ID, PR number/head SHA, and persisted batch deadline.
Its signature is HMAC-SHA256 over `b"deferral:" + body` with the existing dispatch secret.
The gateway verifies the original dispatch receipt and current workflow policy, repository, candidate lineage, and selected member before recording a single durable deferral intent for that attempt.
The recovery deadline remains bounded to one day after the original attempt deadline, allowing a timed-out attempt to be diagnosed within the batch's remaining budget.
The gateway uses its configured App with repository-scoped `merge_queues: write`, plus read-only contents, metadata, and pull requests.
Neither the gateway nor Robin repeats an uncertain removal: Robin reads GitHub to confirm the outcome and requires a fresh validation attempt for the surviving candidate.

Deploy the gateway's deferral endpoint and durable receipt support before deploying Robin's caller.
Verify actual removal in a canary queue after both sides are deployed before broader rollout.

Only confirmed removals generate author-tagged notices.
Missing author mappings route to the configured operator.
Notices include attribution, evidence, a suggested fix, and requeue instructions; pending notices and receipts live with the sequence.
Slack metadata and a stable client message ID reconcile uncertain sends.
Delivery failures retry through the existing job queue and remain eligible for reconciliation; inspect worker errors if Slack access needs restoring.
Routine waiting produces no notifications.

Robin can resume only its exact current outage pause.
Repeating `paused=true` preserves the current pause ownership.
An explicit `manual_hold=true` revokes Robin's ownership and requires a human to resume.
Pausing a running queue through the operator endpoint always creates a manual hold.
Follow-ups use their own scheduling cadence (1–60 minutes) and survive batch expiry; they do not spend validation attempts.
Resume preserves original deadlines and budgets and never requeues ejected PRs.
Escalation alone does not pause other work; an ambiguous dequeue still pauses for operator review.
Failed tests remain strict: no test exception, passing-check override, force merge, or automatic fix PR is enabled here.

Removing `recovery` stops automated work.
Changing its channel/operator invalidates old thread authority; an operator must handle any existing recovery sequence before changing that binding.
Existing pause/resume controls remain available.

### Operator endpoints

All endpoints use dashboard session cookies and the dashboard mutation-origin check.
Bearer/API-key credentials do not authenticate these endpoints.
Access is limited to the configured repository binding and current GitHub repository access.

Under `/dashboard/api/merge-queue/{routing_id}`:

- `GET` lists active requests in `items`, pause state and the active batch ID.
  Use `page_size` (default 20, maximum 100) and the returned `next_cursor` as `cursor` to continue; `next_cursor: null` ends the list.
  Ordering is priority descending, then creation time and ID.
  Pages reflect the live queue: restart without a cursor when refreshing after priority changes, which can move entries across the boundary.
  `GET /batches/{batch_id}` shows candidate and attempt status.
  Both reads use a consistent database snapshot without taking the queue's write locks.
- `POST /requests` accepts `number`, `head_sha`, an idempotency UUID `request_key`, and optional PR-number `dependencies`.
  It requires repository write access and records intent before reviews finish.
  The worker separately checks admission.
- `DELETE /requests/{request_id}` withdraws the caller's intent.
  Operators may withdraw another person's intent.
  Withdrawal invalidates an affected batch.
- `PATCH /pause` accepts `paused` and optional `manual_hold` (default `false`).
  Repeating `{"paused": true}` preserves automatic recovery; `{"paused": true, "manual_hold": true}` takes manual control of an existing pause.
  A new operator pause is always manual, and `{"paused": false}` resumes it.
  `manual_hold=true` requires `paused=true`.
- `PATCH /requests/{request_id}/priority` accepts `priority` and only affects unbatched requests.
  Both mutations require an operator email from the current stored user identity and repository write access.

Registration starts paused.
An operator resumes explicitly.
Pause stops new batch/dispatch work; already dispatched validation and cleanup can still reconcile.
Startup registers a keyed minute job, retaining existing requests, pause state, and running job claims.
GitHub failures are retried by subsequent reconciliation; there is no one-time GitHub availability requirement during startup.

## Ownership handoff

### Integration-routing cutover

Treat migration from environment-owned routing to flat integration bindings as a coordinated cutover, not a rolling queue-runner upgrade.
Old and new routing edits are not dual-written.

1. Quiesce admin routing writes through the dashboard, API, and agent tools before applying the routing migration.
1. Pause queue admission, then stop and drain old schedulers and workers on every replica.
   Reconcile in-flight dispatches and external check/result publication before migrating or starting the new runtime; pausing alone does not stop that work.
1. Apply the migration and verify repository bindings, workflow-start grants, Slack modes, and the queue's new routing binding before resuming writes or queue processing.

Binding-qualified job keys, configuration-generation checks, and native provenance checks fence work handled by the new runtime only.
They do not fence an older binary or an external publisher already in flight.
Never run old and new queue runners concurrently.

### Queue-owner changes

Enable only after inventorying and stopping the previous queue owner and reconciling its in-flight work.
Import remaining human intent with provenance and fresh admission; never import passing evidence or leases.
Stop old admission, scheduler and worker paths on every replica.
Retain repository access needed by the restricted gateway, while ensuring no legacy controller can resume.
Configuration removal alone is not an immediate fence for running replicas.
Never run two queue owners concurrently.
Keep deployment identifiers, credentials, rulesets and live evidence in private infrastructure tooling.
Native mode delegates eventual merging to GitHub and must not enable a custom promotion executor.
Neither mode grants the agent-accessible App queue bypass.
