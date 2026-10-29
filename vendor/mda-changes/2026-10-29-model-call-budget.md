# Bounded model calls: retries, deadlines and a fallback in one budget

**Verdict: upstream as-is.**
Every MDA project calling a hosted model needs a stalled provider to fail, retries that don't multiply between SDK and middleware, and a fallback that the primary can't starve of time.

**Changed:** `managed_deepagents/model_calls.py` (new).

## What

- `ModelCallTimeoutError` and `ModelCallTimeoutMiddleware(timeout_seconds)`: a call that outlives its deadline fails instead of hanging. A provider's own `TimeoutError` passes through as itself, so only the deadline's expiry reads as one.
- `disable_sdk_retries(provider, kwargs)` turns off the SDK retries of OpenAI, Anthropic, Fireworks and Google GenAI chat models (Google counts total attempts, so it gets one), warning when it overrides a configured value.
- `ModelRetriesMiddleware(max_retries=5, max_retry_sleep_seconds=60)` retries transient failures (408, 409, 429, 5xx, timeouts, transport and connection errors) of exactly those models, with jittered backoff that honors `Retry-After`. A hint beyond the remaining sleep budget fails the call rather than retrying early. Each retry is logged and streamed as a `model_retry` custom event, without the provider's error text. Other models keep their SDK's retries and get none here.
- `ModelCallBudget(timeout_seconds, first_output_seconds=None).call(request, handler, fallback=..., falls_back=...)`: one elapsed deadline over a call, its retries and its fallback. With a fallback, the primary call as a whole has two thirds of the budget to start streaming, so a silent stall leaves the rest to the fallback; once it streams, that window is lifted. `fallback` builds the fallback model only once it is needed, and `falls_back` keeps errors the fallback would hit too on the primary.
- Partial output: when a retry or the fallback replaces an attempt that already streamed, each of its streamed messages is retracted with an empty chunk of the same id carrying `response_metadata["mda_superseded"]`, so a client drops what it showed. Graph state never held the failed attempt, and no tool is replayed.

## Why

Open SWE's runtime had this in `mda/rt`. None of it depends on Open SWE: it is how any managed agent should call a model, so it lives with the rest of the runtime. Open SWE keeps only its choices: the 15-minute budget, which models its catalog builds, the fallback its backend names per call, and its tool-pairing errors that the fallback would hit too.

## Upstream

Propose the module unchanged. Once `main` has it, drop this note and let `uv run scripts/vendor.py` merge it away. Open SWE's imports already use the upstream name.
