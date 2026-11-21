# Audit logs

Robin stores metadata-only audit records in PostgreSQL's `open_swe.audit_logs` table.
The envelope follows LangSmith's current audit schema: `id`, `request_time`, `operation_name`, nullable `operation_succeeded`, `user_id`, `api_key_id`, `workspace_id`, and JSONB `enrichments`.
Robin has no organization or second LangSmith user identity, so those columns are omitted; API-key IDs retain Robin's text representation.

Records have no cascading foreign keys: deleting an account or key must not erase its history.
Application writes are insert-only and duplicate event IDs are ignored.
Time, workspace/time, and operation/time indexes support queries.

## Coverage

- Authenticated dashboard `POST`, `PUT`, `PATCH`, and `DELETE` requests, including API-key and federated GitHub Actions thread requests.
  Operation names use the endpoint name without its `api_` prefix.
  This is **API activity**, not proof of a committed business mutation: some POST endpoints are reads, and HTTP success follows the response status.
- The `save_user_settings`, `save_user_instructions`, `manage_feature_flags`, and `manage_review_approval_mode` tools, whether called by the agent directly or from code mode.
  Read actions are excluded.
  Exceptions and returned `ok: false`, `success: false`, or errors count as failures.
- Integration-binding and global-settings mutations record their verified actor and target resource metadata.
  Shared defaults and image operations have no Robin execution-environment scope.
  Historical workspace IDs remain audit data, not routing configuration.

Human IDs come from verified sessions.
API-key actors use the key ID, not its creator.
Tool records identify an agent, with the initiating user only for a direct user run, and include thread/sandbox provenance where available.
A saved requester context is not evidence of a fresh human action.
Historical `workspace_id` values remain available for audit queries; global operations do not infer a Robin environment from a thread or binding.

Enrichments allowlist actor and execution metadata, HTTP method, route **template**, status, and UUID path resource IDs.
Global settings saves also record `settings_scope` and `settings_changes`: changed field names mapped to `before`/`after` stored values.
Feature-flag booleans (including `model_routing_enabled`) and the human-review assignment timeout retain their values; all non-null text values are `[REDACTED]`.
A null value means the override was unset, restoring defaults, not the effective resolved value.
Unchanged fields, timestamps, and unknown fields are excluded.
These changes are attached only after persistence succeeds, even if a later step fails; a no-op save records an empty map.
A failed comparison read omits the changes rather than inventing previous values.
Concurrent saves can observe the same previous value; this is not transactional change capture.

Request/response bodies, tool arguments/results, query strings, headers, credentials, and exception text are never stored.
No IP address is inferred from untrusted forwarded headers.
Production and preview installations retain their own audit history.

Unauthenticated failures, failures before an actor is bound (including CSRF), GET/read access, login/logout, webhook-driven actions, arbitrary shell commands, and tools not listed above are not covered.
There is no historical backfill.

## Querying

Installation administrators can browse **Administration → Audit logs** (`/admin/audit-logs`) in the dashboard.
The viewer defaults to the last 24 hours, with local-time date inputs and exact-match operation, user ID, API key ID, and workspace ID filters.
Apply filters to start a new query; load more to page through the same fixed range.
Event details include actor and execution metadata and recorded settings changes, preserving redacted and unset values.

Installation administrators can call `GET /dashboard/api/audit-logs` using their dashboard session:

- Required `start_time` and `end_time`: timezone-aware timestamps, ordered, at most 31 days apart.
- Optional `operation_name`, `user_id`, `api_key_id`, and `workspace_id` filters.
- `limit`: 1–100, defaults to 50.
- Pass the returned `cursor` unchanged, with the same filters, to fetch the next page.
  Results sort newest first by `(request_time, id)`; `cursor: null` ends pagination.

The response contains `items` in the native envelope above and `cursor`.
Ordinary users and machine credentials cannot read installation-wide history.
No update/delete audit API is exposed.

## Delivery and retention

Recording is best effort, separately from the business transaction.
Writes are bounded to three seconds, shielded from AnyIO request cancellation, and failures are logged without replacing the original result.
A crash, database outage, or failed write can lose an event; this is not a transactional or tamper-proof compliance ledger.
PostgreSQL administrators can still change rows.
There is no automatic expiry or external export; operators must manage retention and backups for their installation.

### Structured settings and usage tools

`read_settings` and `patch_settings` expose schemas derived from the dashboard's typed global, execution, personal and repository settings models, not arbitrary SQL.
Admins may access every resource and select a target person's immutable UUID.
Nonadmins may access only their own personal settings when the backend supplies `personal_settings_tools_enabled: true` in the trusted run context (default off).
This is a per-run parameter, not a persisted admin setting or a model-supplied tool argument; existing private-thread and ownership checks still apply.
Existing ordinary personal-settings tools retain their access rules.
Unknown fields and wrong types are rejected by existing validators/writers.
Identity and credential metadata are not settings fields.

Reads remain private, with admin-surface authorization for global, execution, repository and other-person resources.
Supported shared writes require a verified sole writer and return only acknowledgements.
Global, execution, repository opt-in and review-style writes use atomic version checks, including against dashboard writes.
Personal settings and repository instructions keep their existing validated partial-write semantics.
Writes use normal audit, cache and side-effect paths.
Saving execution defaults never publishes images or recreates sandboxes.

`read_usage` uses the same recorded analytics as Usage.
Admins may read all users from a private admin surface; nonadmins may read only themselves.
Own-only filters use immutable person IDs and their scoped identity aliases before final ranking, totals and pagination; personal responses omit other-user, reviewer and global ingestion metadata.
Month-to-date starts at midnight UTC on the first; deployment reporting cutover can limit recorded coverage.
Recorded costs may be incomplete and exclude external gateway calls.
For the caller only, the tool also reuses the Usage page's authorized live gateway-policy counters, preserving policy-window resets and authorization/unavailability statuses instead of treating these as arbitrary-period totals.
Other-user/deployment-wide live gateway spend is not supported.

These resources are Robin instance/user/repository records, not Smith organization/tenant tables.
Existing admin, private-owner and repository-access boundaries apply; no raw database tool, tenant-table access or cross-person credential grant is added.
