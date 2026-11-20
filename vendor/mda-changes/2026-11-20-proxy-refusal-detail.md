# Upstream as-is.

`packages/pypi/src/managed_deepagents/_sandbox_proxy.py`: a proxy config write the sandbox API refuses raises `ProxyConfigRefusedError`. Its message carries the HTTP status and the API's `detail`, with credential-shaped text removed and capped at 300 characters. `SandboxProxyConfigSync.sync` re-raises it with that message rather than the bare "Could not update sandbox proxy credentials."

Robin needs it because prod runs died on HTTP 422s from `PATCH /v2/sandboxes/boxes/<name>` when MDA started writing every sandbox's proxy config (2026-11-20), and nothing said which part of the config the API rejected. The cause turned out to be cloud identity rules a sandbox wasn't created with.

Upstream: propose it unchanged; drop ours once MDA reports why a proxy config write was refused.
