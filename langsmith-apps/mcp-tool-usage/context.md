# context.md

## What it does

Ranks the most-called MCP tools in an Open SWE tracing project (default `open-swe-v3`) over 24h or 7d. Clicking a bar shows a representative thread that used the tool, focused on that tool's calls and results, with a toggle for the full thread.

## Files

- `src/api.ts`: every LangSmith call, plus choosing the representative thread.
- `src/App.tsx`: project picker, window toggle, two-column layout.
- `src/components/ToolRanking.tsx`: Macaw `TopList` in a `ChartCard`.
- `src/components/ThreadView.tsx`: thread rendering and example cycling.
- `src/lib/messages.ts`: trajectory parsing and the focused-view row builder.
- `src/lib/tools.ts`: splits a run name into connection and tool.

## Data

- **What counts as an MCP call:** Open SWE sets `metadata.mcp_tool_name` on every MCP tool run (`agent/mcp/runtime.py`). The filter is `and(eq(run_type, "tool"), eq(metadata_key, "mcp_tool_name"))`.
- **Run names:** `mcp_<connection>_<tool>_<10-hex>`, truncated to 53 characters before the hash. Connection is taken as the text before the first `_`. That's correct for current connections (they use hyphens), but it would mis-split a connection name that contains `_`.
- **Counts:** come from `POST /api/v1/charts/preview`, grouped by `name`, computed server-side over the whole window.
  - `max_groups` is capped at 20.
  - `stride` is capped at 7 days.
  - A window slightly over one stride returns two buckets, so values are summed per group.
- **Representative thread:** from `POST /api/v2/runs/query`, the 200 most recent calls of the tool, grouped by `THREAD_ID`.
  - Threads where any call failed sort last.
  - Then threads are ordered by how close their call count is to the median.
- **Failed calls:** MCP failures come back as `status: success`, with an output preview like `tool: Error: …` or `tool: Input validation error: …`, so errors are detected from `OUTPUTS_PREVIEW`.
- **Thread messages:** `POST /v1/trajectory` with `format: "messages"`.
  - AI content blocks are `text`, `reasoning` or `tool_call` (`id`, `name`, `args`).
  - Tool messages link back to their call via `tool_call_id`.
  - Human turns wrap text in `<input-message>` with an HTML-escaped body.
  - Open SWE also injects `<dynamic-context>` human turns. The focused view skips these when picking the prompting message.

## Gotchas

- The sandbox loads only `dist/bundle.js`. `vite.config.ts` sets `codeSplitting: false`, because CodeLite lazy-loads its language modules.
- A TopList bar reacts to real `pointerup` events on the row `<g>`. Automation that dispatches raw coordinate clicks may miss it; clicking the label text works.
- `langsmith apps dev` keeps its OAuth token for its whole life. If calls start returning 401, run any `langsmith` CLI command to refresh the token, then restart the dev server.
- Types come from the `langsmith` npm package where they exist (`TracerSession`, `Run`). Chart-preview, trajectory and v2 query-only fields (`thread_id`, `outputs_preview`) aren't in the SDK, so they're typed locally.
