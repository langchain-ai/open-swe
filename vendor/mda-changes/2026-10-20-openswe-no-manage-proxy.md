# `_openswe_no_manage_proxy`: leave the sandbox's proxy config alone

**Verdict: not upstream as-is.** A stopgap: it turns off MDA's proxy handling for the sandbox altogether.

**Changed:** `managed_deepagents/namespaces.py` (`define_sandbox`), `sandbox.py` (`manages_proxy`, `RESOLUTION_OPTION_KEYS`), `runtime.py` (`_sync_sandbox_proxy_config` reads it from the run's resolved sandbox). Open SWE sets it in `mda/rt/managed_sandbox.py`.

## What

`define_sandbox(_openswe_no_manage_proxy=True)` (default `False`) stops MDA from writing the sandbox's proxy config before each command. Declaring `proxy_config` with it is rejected.

## Why

Before every command it runs in the sandbox, MDA replaces the sandbox's whole proxy config with the one the project declares, or with no rules. Open SWE keeps two rules on the sandbox: the thread's repository-scoped GitHub token for `github.com`/`api.github.com`, and the tool channel's capability header. The first command MDA ran itself, such as the browser saving a screenshot, would wipe them.

**Cost:** on these sandboxes no MDA connection, channel or connector can inject credentials through the proxy: the only config written there is Open SWE's.

Open SWE sets it on every run's sandbox. Its backend writes none of the sandbox's proxy config: MDA writes all of it, as the backend hands it over, GitHub's rules with a token it minted for the run's scope and the backend's own rules for the sandbox, so one writer still owns every rule. That config names the sandbox, so it is known only once the sandbox is open, after MDA resolved the run's sandbox through `define_sandbox(resolve=...)`, which it does once per run. So Open SWE's `SandboxProxyWriter` writes it through MDA's own `SandboxProxyConfigSync` (`managed_deepagents._sandbox_proxy`) once the sandbox has booted, outside any command, and Open SWE's resolver returns every run's sandbox with this option, which MDA merges over the declaring definition: MDA's per-run write before the run's first command leaves the config alone instead of writing one with no rules over it. An external sandbox keeps the config its harness gave it.

## Upstream

Each writer should own only its rules and leave the rest:

- **LangSmith sandboxes:** an atomic per-namespace rule write, so writers never read-modify-write the whole config.
- **MDA:** write its rules under its own namespace and never touch the others, and offer a call that writes them now, outside a command.
- **Open SWE:** write its rules under its own namespace, then drop this option.

Separately, MDA keeps one sandbox per thread but resolves user-owned connections for whoever started the current run. Code running in a shared sandbox can use one user's injected credentials, or keep what they fetched, during another user's run. MDA should inject only credentials the thread's sandbox may hold for everyone who runs in it, or give each user a separate sandbox.
