# `_openswe_api_endpoint`: keep the box in another LangSmith

**Verdict: not upstream as-is.** It points one definition's box at a LangSmith other than the one MDA deploys to, which MDA's deploy, bake and cleanup do not know about.

**Changed:** `managed_deepagents/namespaces.py` (`define_sandbox`), `sandbox.py` (`api_endpoint`, `normalize_sandbox_options`), `runtime.py` (`_sandbox_client_kwargs`, `_validated_sandbox_api_endpoint`), `_sandbox_proxy.py` (`patch_sandbox_proxy_config`, which now takes the endpoint from the client kwargs). Open SWE sets it in `mda/rt/managed_sandbox.py` from `SANDBOX_LANGSMITH_ENDPOINT` and `SANDBOX_LANGSMITH_API_KEY`.

## What

`define_sandbox(_openswe_api_endpoint=<full sandbox API URL>, api_key=<that LangSmith's key>)` makes MDA create, adopt, start, replace and delete the box at that endpoint with that key, instead of at the deploy-owned `MDA_SANDBOX_API_ENDPOINT`. It needs `api_key`, and it drops the deployment's `X-Tenant-Id` (`LANGSMITH_WORKSPACE_ID`), which names a workspace in the deployment's own LangSmith; the key's own workspace applies. The URL gets the same checks as `MDA_SANDBOX_API_ENDPOINT`.

## Why

Open SWE wants its thread sandboxes in a different LangSmith region (AWS US prod) from the one the MDA deployment and its tracing live in (GCP US prod). The backend binds the thread to the box MDA reports and then reaches it itself, for its proxy config, snapshots and service URLs, so both must use the same LangSmith. `MDA_SANDBOX_API_ENDPOINT` is reserved and always set to the deploy's own platform API, so a deployment cannot move its boxes.

**Cost:** boxes there are outside what MDA knows of its deployment: `mda delete` does not find them, a `setup.sh` recipe is baked in the deployment's LangSmith and cannot be used with it, and MDA's browser stays in the deployment's LangSmith.

## Upstream

MDA should let a deployment choose its sandbox region (or LangSmith) as deployment configuration, with deploy preflight, recipe bake and `mda delete` all using it. Open SWE then sets that instead and drops this option.
