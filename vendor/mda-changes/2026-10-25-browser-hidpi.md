# `BrowserMiddleware(hidpi=...)`: create browsers that render at twice the resolution

**Verdict: upstream as-is.**

**Changed:** `managed_deepagents/browser.py` (`BrowserMiddleware.__init__`, `_open`).

## What

A `hidpi` option, off by default, that `_open` sends with `POST /browsers`. LangSmith fixes it when it creates a browser, so it has no effect on one that already exists.

## Why

Open SWE frames each thread's browser window in a narrow panel, zoomed in to follow the pointer, where the default resolution looks soft. `mda/rt/browser.py` turns it on, as the backend does when a person opens the browser before the agent has, so a thread's browser is HiDPI whichever side creates it.

## Upstream

Propose the option unchanged. Once MDA has it, this edit merges away.
