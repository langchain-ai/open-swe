Execute an arbitrary PostgreSQL statement against the Open SWE application database, including DDL, bulk writes, and migration metadata repairs.

Only configured admins on private admin surfaces may use this tool, and only in preview or staging.
Before executing, show the requester the exact SQL, target environment, and expected effects, and obtain their explicit confirmation.
Pass `confirm=true` only after that confirmation; confirmation of a different statement or environment does not count.
Never infer confirmation from a general request to investigate or fix a problem.

Submit one statement per call; use a single statement with CTEs when a repair must update multiple tables atomically.
The statement runs in a transaction with the Open SWE schema on the search path, a 60-second statement timeout and a 3-second lock timeout.
There is no table allowlist or affected-row limit.
Failed statements roll back; the connection is discarded after each call so session changes cannot leak to the application pool.
Commands requiring execution outside a transaction are not supported.
The result reports the affected row count (or -1 when PostgreSQL does not report one); query rows are not returned.
Use `read_only_sql` for diagnostics and to verify the repair afterward.
Do not include secrets or credentials in SQL or expose sensitive data through query text or errors.
