# Stable box names under `mda dev`

**Verdict: upstream as-is.** A fix any project running `mda dev` benefits from.

**Changed:** `managed_deepagents/runtime.py` (`_managed_deployment_name`, `_local_dev_deployment_name`).

## What

With no deployment name, which is the case under `mda dev`, MDA names boxes `mda-dev-<digest of machine and project directory>--<thread digest>` instead of leaving them unnamed. Only box names fall back to it: a name read from a given env, as Studio links read it, stays the deployment's or none.

## Why

An unnamed box is found only through the process's cache, so restarting `mda dev` gives every thread a new, empty box and strands the old ones.

## Upstream

As it stands, unchanged.
