# Temporary analytics cutover rehearsal

This directory and its workflow belong to an **unmerged validation PR on top of
#2687**. Keep the PR through production verification, then close it. Do not merge
the fixture application or its worker-control endpoint into the product.

The rehearsal launches the real LangGraph development server in separate
processes with the blocking-call detector enabled. It preserves the dev runtime's
Store, checkpoints, and scheduled work across process replacement, and uses an
isolated PostgreSQL database for the production analytics migrations and worker.
Dashboard requests use signed local sessions; merge events use signed webhooks.
No model, GitHub, or LangSmith service credentials are needed.

It exercises:

- Synthetic historical Store usage stays intact and is excluded from new reports.
- Operational preferences, a checkpointed thread, and scheduled work survive the
  switch from a fixture app without analytics to the new production app.
- A Store-only run finishing after cutover stays excluded. A PR opened before
  cutover and merged afterward stays outside the new PR cohort.
- New capture helpers enqueue activity from a synthetic graph invoked over HTTP.
  The real worker delivers it to production reporting HTTP routes.
- An application restart recovers queued events. Repeated merge deliveries count
  once. Another restart preserves the cutover, costs, and PR outcomes.

This is a synthetic-data rehearsal, not a deployment of an old production image.
It does not prove a zero-downtime mixed-version rollout or exercise model billing,
sandbox creation, GitHub OAuth, or the production LangGraph persistence backend.
Drain old runs before cutover; do not interpret the intentional exclusion of a
Store-only in-flight run as support for overlapping old and new instances.
Reusable delivery-failure tests and long-term PostgreSQL CI belong in the
permanent stack, separately from this disposable rehearsal.

## Run

Use a disposable local PostgreSQL instance. The configured role must be able to
create databases; the fixture creates and drops a uniquely named database and
never migrates the database named in the supplied URL.

```sh
RUN_ANALYTICS_CUTOVER_VALIDATION=1 \
TEST_ANALYTICS_POSTGRES_URI='postgresql+asyncpg://cutover@127.0.0.1:5432/cutover_admin?ssl=disable' \
uv run --no-sync pytest tests/cutover_validation -q \
  --basetemp=.cutover-validation --junitxml=cutover-results.xml
```

Without the opt-in flag, ordinary test runs skip this temporary directory. With
the flag, a missing PostgreSQL URL is an error. Its dedicated CI workflow also
rejects skipped tests and retains server logs and JUnit results.

The fixture uses fresh random local signing keys, loopback ports, and a filtered
child-process environment. It does not load the developer's `.env`. Server logs
are under the specified pytest temporary directory. The runtime state there is
synthetic and can be deleted after inspection.

Rebase this PR onto the latest #2687 before trusting its results. Fix product
bugs in their underlying stack PR, then rerun this rehearsal. After merging the
stack, retain this PR unmerged until the production cutover is verified.
