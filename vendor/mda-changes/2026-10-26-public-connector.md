# `managed_deepagents.Connector`: connectors as public Python API

**Verdict: upstream as-is.**

**Changed:** `managed_deepagents/__init__.py` (exports `Connector`, `SandboxContext`, `SandboxMode`).

## What

The package root exports the connector base class and the context its `sandbox` hook gets, as the TypeScript SDK exports its connector contract. `mda build` already discovers Python connectors under `connectors/`; only the names were private (`managed_deepagents._connector`).

## Why

`mda/connectors/box_setup.py` readies the thread's box (the bot's git identity, the work dir) from a `sandbox` hook, so it runs when MDA creates or first adopts the box in a process instead of on every turn from the backend.

## Upstream

Propose the exports unchanged. Once MDA has them, this edit merges away.

The generated `_mda_connectors` module calls connectors under `connectors/` legacy, and the supported way to ready a box, `sandbox/setup.sh`, runs only when MDA bakes its own recipe snapshot, so it never reaches a box created from a snapshot the project names (`snapshot_id`), as every Open SWE box is. MDA should offer a supported hook that runs when it creates or first adopts a box, whatever its snapshot; `box_setup.py` then moves to it.
