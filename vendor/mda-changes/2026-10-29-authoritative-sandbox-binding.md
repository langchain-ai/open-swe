# Resolve the persisted sandbox binding and permit explicit failed-box replacement

**Verdict: not upstream as-is.** `_openswe_sandbox_id` carries Open SWE's authoritative persisted sandbox name, not an MDA platform binding.

**Changed:** `managed_deepagents/runtime.py` and `managed_deepagents/sandbox.py`.

## What

Resolve the persisted name before considering canonical names, ignoring a cached handle for a different name. A missing or unusable bound sandbox fails closed without creating another workspace. This reconnects legacy suffixed bindings without selecting, copying, or deleting either workspace in an existing split.

A not-found tool failure can reconnect to a newer sandbox ID under the same name, but cannot create a replacement for a per-thread sandbox. Explicit replacement no longer requires opening a usable backend first: it looks up the bound name (or the cached/canonical name when unbound), retires that box even when failed, and creates its authorized replacement under the same name.

## Why

Disabling recovery entirely stranded cached handles after an explicit replacement on another replica. Strict adoption also prevented explicit replacement of failed boxes after a runtime restart, while canonical-only lookup stranded threads already bound to a suffixed workspace.

## Upstream

MDA should persist an authoritative binding shared across replicas, reconcile stale handles without implicit recreation, and allow explicit replacement independently of box health. Once that contract exists, Open SWE can remove its private binding option.
