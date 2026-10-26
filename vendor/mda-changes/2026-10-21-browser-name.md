# `BrowserMiddleware.browser_name`: name a run's browser before opening it

**Verdict: upstream as-is.**

**Changed:** `managed_deepagents/browser.py` (`BrowserMiddleware.browser_name`).

## What

A public method returning the name of the LangSmith browser a run's browser tools use, from the same key and deployment prefix `_call` uses. It neither creates nor opens the browser. It names the key's first browser: `_call` moves to a later one only when that one is being deleted or broke, and `_share` then reports the name it opened.

## Why

Open SWE lets a person open a thread's browser from its Browser panel before the agent has browsed. Its backend creates the browser under MDA's name with the `mda` window, which MDA's `_open` then adopts, so the person and the agent share one browser. Only MDA knows that name, since it is derived from the deployment, so `mda/rt/browser.py` reports it when each run starts.

## Upstream

Propose the method unchanged. Once MDA has it, this edit merges away.
