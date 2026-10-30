# `_openswe_no_manage_proxy`: leave the sandbox's proxy config alone

**Verdict: not upstream as-is.** A stopgap: it turns off MDA's proxy handling for the sandbox altogether.

**Changed:** `managed_deepagents/namespaces.py` (`define_sandbox`), `sandbox.py` (`manages_proxy`), `runtime.py` (`_sync_sandbox_proxy_config`). Open SWE sets it in `mda/rt/managed_sandbox.py`.

## What

`define_sandbox(_openswe_no_manage_proxy=True)` (default `False`) stops MDA from writing the sandbox's proxy config before each command. Declaring `proxy_config` with it is rejected.

## Why

Before every command it runs in the sandbox, MDA replaces the sandbox's whole proxy config with the one the project declares, or with no rules. Open SWE's backend keeps two rules on the same sandbox: the thread's repository-scoped GitHub token for `github.com`/`api.github.com`, and the tool channel's capability header. The first command MDA ran itself, such as the browser saving a screenshot, would wipe them.

**Cost:** on these sandboxes no MDA connection, channel or connector can inject credentials through the proxy, since MDA no longer writes any rules there.

Open SWE sets it only on the sandboxes whose proxy config its backend writes. Where the backend names `MDA_GITHUB_PROXY_CONNECTION` and the thread's GitHub token is not narrowed, the run's sandbox definition declares the whole config instead: GitHub's rules from that connection and the backend's own rules for the sandbox. The backend then writes none of it, so one writer still owns every rule. To have MDA write it before the backend uses the sandbox, Open SWE runs a no-op command through a fresh handle from the hook backend's `bind_runtime`: the hook's own handle keeps the sandbox definition it resolved first, from before the backend's rules were known.

## Upstream

Each writer should own only its rules and leave the rest:

- **LangSmith sandboxes:** an atomic per-namespace rule write, so writers never read-modify-write the whole config.
- **MDA:** write its rules under its own namespace and never touch the others, and offer a call that writes them now, outside a command.
- **Open SWE:** write its rules under its own namespace, then drop this option.

Separately, MDA keeps one sandbox per thread but resolves user-owned connections for whoever started the current run. Code running in a shared sandbox can use one user's injected credentials, or keep what they fetched, during another user's run. MDA should inject only credentials the thread's sandbox may hold for everyone who runs in it, or give each user a separate sandbox.
