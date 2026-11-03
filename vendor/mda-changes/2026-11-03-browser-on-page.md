# `BrowserMiddleware.on_page`: a subclass's own browser tools

**Verdict: upstream as-is.**

**Changed:** `browser.py` (`BrowserMiddleware.on_page`).

## What

`on_page(runtime, tool_name, fn)` runs `fn` on the page the browser tools act on and returns it as tool `tool_name`'s reply.
The call goes through the same path as `browser_run`'s: it waits while the user has the window, opens or reopens the run's browser, turns errors into the model's reply, and notes a new browser sandbox in state.

## Why

Open SWE's `browser_sign_in` (`mda/rt/browser.py`) signs the browser in with a session an MCP result carried, which the model only knows by a handle.
It needs to act on the run's page as the built-in tools do, without the session ever passing through `browser_run`'s code argument, and so through the model and its traces.
Without a public hook it had to wrap `browser_run`'s coroutine and import the private `_Session` and `_reply`.

## Upstream

Propose `on_page` unchanged.
Once MDA has it, this note goes; Open SWE already calls it by that name.
