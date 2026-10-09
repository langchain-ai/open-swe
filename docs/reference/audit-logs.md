# Audit logs

Open SWE stores metadata-only audit records in PostgreSQL's `open_swe.audit_logs` table. The envelope follows LangSmith's current audit schema: `id`, `request_time`, `operation_name`, nullable `operation_succeeded`, `user_id`, `api_key_id`, `workspace_id`, and JSONB `enrichments`. Open SWE has no organization or second LangSmith user identity, so those columns are omitted; API-key IDs retain Open SWE's text representation.

Records have no cascading foreign keys: deleting an account, key, or workspace must not erase its history. Application writes are insert-only and duplicate event IDs are ignored. Time, workspace/time, and operation/time indexes support queries.

## Coverage

- Authenticated web `POST`, `PUT`, `PATCH`, and `DELETE` requests, including API-key and federated GitHub Actions thread requests. Operation names use the endpoint name without its `api_` prefix. This is **API activity**, not proof of a committed business mutation: some POST endpoints are reads, and HTTP success follows the response status.
- The `save_user_settings`, `save_user_instructions`, `manage_feature_flags`, and `manage_review_approval_mode` tools, whether called by the agent or through the sandbox tool endpoint. Read actions are excluded. Exceptions and returned `ok: false`, `success: false`, or errors count as failures.
- Workspace lifecycle, repository-configuration, and workspace-settings HTTP operations attach the target workspace ID before deletion or after creation. Feature-flag tool writes attach their explicit target workspace; instance-wide writes have no workspace ID. Other operations may have no workspace scope.

Human IDs come from verified sessions. API-key actors use the key ID, not its creator. Tool records identify an agent, with the initiating user only for a direct user run, and include thread/sandbox provenance where available. A saved requester context is not evidence of a fresh human action. Tool `enrichments.workspace` can describe the execution workspace; `workspace_id` is set only for explicitly bound scopes.

Enrichments allowlist actor and execution metadata, HTTP method, route **template**, status, and UUID path resource IDs. Instance and workspace settings saves also record `settings_scope` and `settings_changes`: changed field names mapped to `before`/`after` stored override values. Feature-flag booleans (including `model_routing_enabled`) and the human-review assignment timeout retain their values; all non-null text values are `[REDACTED]`. A null value means the override was unset, restoring defaults or inheritance, not the effective resolved value. Unchanged fields, timestamps, and unknown fields are excluded. These changes are attached only after persistence succeeds, even if a later step fails; a no-op save records an empty map. A failed comparison read omits the changes rather than inventing previous values. Concurrent saves can observe the same previous value; this is not transactional change capture.

Request/response bodies, tool arguments/results, query strings, headers, credentials, and exception text are never stored. No IP address is inferred from untrusted forwarded headers. Production and preview installations retain their own audit history; a workspace override additionally identifies its workspace.

Unauthenticated failures, failures before an actor is bound (including CSRF), GET/read access, login/logout, webhook-driven actions, arbitrary shell commands, and tools not listed above are not covered. There is no historical backfill.

## Querying

Installation administrators can browse **Administration → Audit logs** (`/admin/audit-logs`) in the web app. The viewer defaults to the last 24 hours, with local-time date inputs and exact-match operation, user ID, API key ID, and workspace ID filters. Apply filters to start a new query; load more to page through the same fixed range. Event details include actor and execution metadata and recorded settings changes, preserving redacted and unset values.

Installation administrators can call `GET /api/audit-logs` using their web session:

- Required `start_time` and `end_time`: timezone-aware timestamps, ordered, at most 31 days apart.
- Optional `operation_name`, `user_id`, `api_key_id`, and `workspace_id` filters.
- `limit`: 1–100, defaults to 50.
- Pass the returned `cursor` unchanged, with the same filters, to fetch the next page. Results sort newest first by `(request_time, id)`; `cursor: null` ends pagination.

The response contains `items` in the native envelope above and `cursor`. Ordinary users and machine credentials cannot read installation-wide history. No update/delete audit API is exposed.

## Delivery and retention

Recording is best effort, separately from the business transaction. Writes are bounded to three seconds, shielded from AnyIO request cancellation, and failures are logged without replacing the original result. A crash, database outage, or failed write can lose an event; this is not a transactional or tamper-proof compliance ledger. PostgreSQL administrators can still change rows. There is no automatic expiry or external export; operators must manage retention and backups for their installation.
