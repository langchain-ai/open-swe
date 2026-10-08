# Incident persistence cutover

Incidents use `open_swe.incident_record` in the application's `POSTGRES_URI` database.
The `(kind, key)` primary key keeps the existing opaque keys; JSONB payloads retain
policies, incidents, reports, command receipts, history metadata, and curated markdown.
Normal incident reads and writes never access the LangGraph Store or fall back to it.
Database failures propagate rather than appearing as empty records.

## Preserve an existing deployment

1. Pause incident ingress, responder commands, settings edits, and incident agent runs.
   Keep writers paused through the complete cutover; offset-based legacy paging requires
   a stable source. Do not enable the new incident application yet.
2. Run the new code's backfill with `POSTGRES_URI` pointing to the destination database
   and `LANGGRAPH_API_KEY` (or `LANGSMITH_API_KEY`) authorizing access to the old Store:
   `uv run python -m openswe.incidents.backfill --source-url https://legacy-deployment.example`.
   Supply the real deployment URL; the CLI explicitly connects remotely rather than using
   the SDK's default in-process transport.
   The command applies database migrations, then pages each of the six legacy namespaces
   (`incidents/policies`, `incidents/incidents`, `incidents/reports`, `incidents/commands`,
   `incidents/history`, and `incidents/summaries`). It copies keys and payloads verbatim,
   rejects malformed records, and ignores nested namespaces. It never deletes source data.
3. Verify the destination contains the source keys and payloads in all six kinds before
   starting the new application. A failed backfill can be rerun: inserts are idempotent and
   existing destination rows are never overwritten. If an earlier partial copy was made
   while legacy writers were still active, reconcile those rows before cutover; rerunning
   does not refresh existing destination payloads.
4. Deploy the new application, stop all old writers, and resume incident traffic only on
   the new version. Do not run old and new incident writers simultaneously.

New deployments need only the normal application database migrations. Legacy data is
retained for operator recovery, but it becomes stale after cutover. Rolling back to an old
version after new writes requires copying/reconciling those writes back first; there is no
automatic reverse migration or dual-write mode.
