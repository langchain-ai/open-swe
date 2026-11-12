# Not upstream as-is.

`packages/pypi/src/managed_deepagents/_memory_instructions.py` removes the
middleware's closure-level hot-memory cache and reloads memory on each model call.
Memory injection stays outside thread state, in the same slot after prompt-cache
middleware, with the existing provider cache markers.

Robin needs fresh caller memory on the next run. MDA reuses compiled graphs, so the
old cache outlived the run that populated it: a later run, even in a different
thread, received stale memory after a successful save. The random-number browser
regression reproduces this across two completed, separate runs.

This is a temporary workaround, not a requirement to reload memory after saving it
within the same run. It adds a memory read per model call and can change the memory
suffix mid-run. Caller authorization and storage scoping are unchanged; no new
options or functions are introduced.

Upstream: load hot memory once per run, reuse that snapshot within the run, and
reload on the next run while retaining compiled-graph reuse and concurrent-run
isolation. Drop this workaround once MDA provides that lifecycle; keep the
cross-run recall regression.
