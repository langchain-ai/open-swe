# Describe the configured interpreter output limit

**Verdict: upstream as-is.** The prompt should describe the same limit the interpreter enforces.

**What:** `langchain_quickjs/middleware.py` passes `max_result_chars` to the base prompt renderer in `_prompt.py`.
The prompt states the limit for both stdout and returned results, recommends compact summaries or slices and direct file reads, and removes the claim that returned values are uncapped.

**Why:** Open SWE raises its interpreter limit to 16,000 characters so ordinary tool results survive beyond 4,000 characters, while keeping the existing truncation markers for oversized output.
The model needs accurate guidance to avoid repeating tool calls after truncation.

**Upstream:** Drop this change once upstream renders the configured output limit and corrects the uncapped-result guidance.
