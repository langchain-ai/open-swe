**Not upstream as-is.**

**What:** The lazy sandbox backend in `packages/pypi/src/managed_deepagents/runtime.py` writes large tool results to deepagents' existing state backend when `_openswe_select` chooses no sandbox. Open SWE declares the files state channel and routes box-less file-tool calls under `/large_tool_results/` to that same backend. A replacement filesystem middleware truncates oversized tool content if a box-less eviction raises or returns a write error, logging the failure without claiming a file was saved. Selected-box eviction still propagates failures as before.

**Why:** A shared graph declares a sandbox even for runs whose catalog selects none. Large tool-result eviction must preserve the content without opening a box or failing the run. Runs that select a box still write their results into that box.

**Upstream:** MDA needs filesystem routing for a per-run optional sandbox, including the state files channel and tools that can read offloaded results. Replace this Open SWE selector fallback when that routing is available.
