# `define_sandbox(api_endpoint=...)`: keep the box in another LangSmith

**Verdict: upstream as-is.** It pairs with the existing `api_key` option: a definition can already bring its own key, and now that key's LangSmith too, such as another region.

**Changed:** `managed_deepagents/namespaces.py` (`define_sandbox`, `_LangSmithSandboxArgs`), `sandbox.py` (`normalize_sandbox_options`), `runtime.py` (`_sandbox_client_kwargs`, `_sandbox_create_kwargs`, `_validated_sandbox_api_endpoint`), `_sandbox_proxy.py` (`patch_sandbox_proxy_config`, which now takes the endpoint from the client kwargs). Open SWE sets it in `mda/rt/managed_sandbox.py` when the backend's catalog says the thread's box lives in the new sandbox region, `NEW_SANDBOX_REGION_ENDPOINT`, with `NEW_SANDBOX_REGION_API_KEY`.

## What

`define_sandbox(api_endpoint=<full sandbox API URL>, api_key=<that LangSmith's key>)` makes MDA create, adopt, start, replace and delete the box at that endpoint with that key, instead of at the deploy-owned `MDA_SANDBOX_API_ENDPOINT`. It needs `api_key`, is never sent in the create request, and gets the same URL checks as `MDA_SANDBOX_API_ENDPOINT`. It also drops the deployment's `X-Tenant-Id` (`LANGSMITH_WORKSPACE_ID`), which names a workspace in the deployment's own LangSmith; the key's own workspace applies.

## Why

Open SWE creates new thread sandboxes in a new sandbox region, AWS US prod, while the MDA deployment, its tracing and every box created before stay in GCP US prod. Each box stays where it was created, so the definition, which MDA builds per run, names the box's LangSmith: the backend records it with the thread and tells MDA in the catalog. The backend binds the thread to the box MDA reports and then reaches it itself, for its proxy config, snapshots and service URLs, so both must use the same LangSmith. `MDA_SANDBOX_API_ENDPOINT` is reserved and always set to the deploy's own platform API, so without this a deployment cannot move its boxes.

## Upstream

Propose the option unchanged, plus what Open SWE does not need but other projects would:

- The TypeScript `defineSandbox` gets the same option.
- `mda deploy` runs its sandbox-access preflight and bakes `setup.sh` at the definition's endpoint, or rejects a recipe with it.
- `mda delete` finds the boxes there too.

Once upstream has it, this edit merges away.
