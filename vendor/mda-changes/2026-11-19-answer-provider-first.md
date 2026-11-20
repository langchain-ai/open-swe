# Answer the provider first, then ask the channel's handler

**Verdict: upstream as-is.** It was langchain-ai/managed-deepagents-sdk#1149, closed when MDA moved into langchainplus.

**Changed:** `managed_deepagents/_channels/github.py`, `linear.py`, `trigger/channel.py` (and docstrings in `http/http.py`, `trigger/http.py`).

## What

GitHub, Linear and Slack (through Trigger Server) get their answer as soon as a delivery is verified, for every handler, not only an endpoint. MDA then asks the channel's `on_event` handler and starts the run. A handler that raises starts nothing, and its error is logged; a Linear agent session hears at once that something went wrong.

## Why

Open SWE's GitHub, Linear and Slack handlers (`mda/channels/`) call its backend before a run starts. GitHub waits 10 seconds and Linear 5, so a handler that runs while the provider waits can miss that deadline, and the delivery fails although the work was fine.

## Upstream

Reopen #1149's change against `sdks/managed-deepagents` (Python and TypeScript), and drop this once it lands.
