# `_openswe_no_manage_proxy`: leave the box's proxy config alone

**Verdict: not upstream as-is.** A stopgap: it turns off MDA's proxy handling for the box altogether.

**Changed:** `managed_deepagents/namespaces.py` (`define_sandbox`), `sandbox.py` (`manages_proxy`), `runtime.py` (`_sync_sandbox_proxy_config`). Open SWE sets it in `mda/rt/managed_sandbox.py`.

## What

`define_sandbox(_openswe_no_manage_proxy=True)` (default `False`) stops MDA from writing the box's proxy config before each command. Declaring `proxy_config` with it is rejected.

## Why

Before every command it runs in the box, MDA replaces the box's whole proxy config with the one the project declares, or with no rules. Open SWE's backend keeps two rules on the same box: the thread's repository-scoped GitHub token for `github.com`/`api.github.com`, and the tool channel's capability header. The first command MDA ran itself, such as the browser saving a screenshot, would wipe them.

**Cost:** on these boxes no MDA connection, channel or connector can inject credentials through the proxy, since MDA no longer writes any rules there.

## Upstream

Each writer should own only its rules and leave the rest:

- **LangSmith sandboxes:** an atomic per-namespace rule write, so writers never read-modify-write the whole config.
- **MDA:** write its rules under its own namespace and never touch the others.
- **Open SWE:** write its rules under its own namespace, then drop this option.

Separately, MDA keeps one box per thread but resolves user-owned connections for whoever started the current run. Code running in a shared box can use one user's injected credentials, or keep what they fetched, during another user's run. MDA should inject only credentials the thread's box may hold for everyone who runs in it, or give each user a separate box.
