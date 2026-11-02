# Resolve the persisted sandbox binding

**Verdict: not upstream as-is.**
`_openswe_sandbox_id` carries Open SWE's authoritative persisted sandbox name, not an MDA platform binding.

**Changed:** `managed_deepagents/runtime.py` and `managed_deepagents/sandbox.py`.

## What

Resolve the persisted name before considering canonical names, ignoring a cached handle for a different name.
A missing or unusable bound sandbox fails closed without creating another workspace.
This reconnects legacy suffixed bindings without selecting, copying, or deleting either workspace in an existing split.

A not-found tool failure can reconnect to a newer sandbox ID under the same name, but cannot create a replacement for a per-thread sandbox.

## Why

Disabling recovery entirely stranded stale cached handles on other replicas, while canonical-only lookup stranded threads already bound to a suffixed workspace.

## Upstream

MDA should persist an authoritative binding shared across replicas, reconcile stale handles without implicit recreation, and allow explicit replacement independently of sandbox health.
Once that contract exists, Open SWE can remove its private binding option.
