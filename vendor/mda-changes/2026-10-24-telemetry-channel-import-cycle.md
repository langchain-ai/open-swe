# Defer telemetry's connection imports

**Verdict: upstream as-is.** Telemetry must not prevent the SDK from importing when HTTP provider channels are present.

- **What:** In `packages/pypi/src/managed_deepagents/_telemetry/metadata.py` and `_telemetry/otel/exporter.py`, import connection URL resolution inside the functions that use it rather than at module initialization.
- **Why:** Stable 0.9.5's logger imports telemetry, whose connection helpers import the retained GitHub/Linear provider channels, which import the logger. Deferring these imports breaks that cycle without changing connection lookup or log delivery. Existing `mda/tests/test_backend_calls.py` coverage failed during collection before this fix and imports the SDK successfully afterward.
- **Upstream:** Keep telemetry initialization independent of provider channel initialization when merging the HTTP channels into `main`; then remove this patch.
