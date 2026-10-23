# The agent's browser window is attached to the thread's sandbox

**Verdict: upstream as-is.** Every MDA project that uses a thread's browser and sandbox together benefits from both driving the same window.

**Changed:** `managed_deepagents/browser.py` (`_open`, `_agent_window`, `_publish_cdp_url`, `BrowserMiddleware._call`; `_share` becomes the `_browsing` hook). Open SWE overrides the hook in `mda/rt/browser.py`.

## What

Once the thread's sandbox exists, the window the agent's `run`, `snapshot` and `screenshot` tools drive (`mda`) is attached to that sandbox with `share_localhost`, instead of a second, blank `mda-localhost` window being attached beside it. The window's `cdp_url`, reachable only over the sandbox's link, is written to `/tmp/mda-browser-cdp-url` in the sandbox, and `run`'s description tells the agent where to find it. A window attached to no sandbox or to a replaced one is closed and reopened, and a leftover `mda-localhost` window is closed. If attaching the sandbox fails, the window opens without it, as before.

## Why

The sandbox could only reach the `mda-localhost` window over CDP, and that window's endpoint is filtered to its own tabs. Code in the sandbox (Playwright, `agent-browser --cdp`, a test harness) could not see or drive the page the agent's tools had open.

**Cost:** the window now closes, tabs and all, when the thread's sandbox is replaced, since detaching a sandbox closes its window.

## Upstream

Propose this on the browser pull request MDA lands (managed-deepagents-sdk#967 and its localhost follow-up). Once `main` has it, let `uv run scripts/vendor.py` merge this edit away and delete this note.
