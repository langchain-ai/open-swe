# `_openswe_create_fields`: create-request fields the SDK does not model

**Verdict: not upstream as-is.** It patches the LangSmith SDK client's transport.

**Changed:** `managed_deepagents/namespaces.py`, `sandbox.py` (`declared_create_fields`), `runtime.py` (`_send_create_fields`).

## What

`_openswe_create_fields={...}` merges the given fields into the body of the request that creates the sandbox, through the SDK client's HTTP transport.

## Why

Open SWE creates every sandbox with `preserve_memory_on_stop: true`, so running processes such as dev servers and background tasks survive an idle stop, plus a workspace's own create parameters and `SANDBOX_CREATE_EXTRA_JSON`. The SDK's `create_sandbox` takes only the fields it models.

## Upstream

The LangSmith SDK's `create_sandbox` should accept `preserve_memory_on_stop` (and the other create fields the API supports), and MDA's `define_sandbox` should pass them through as typed options. Then Open SWE declares them directly and drops the option.
