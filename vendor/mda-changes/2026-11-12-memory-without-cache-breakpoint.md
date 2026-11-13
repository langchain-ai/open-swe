# Not upstream as-is.

`packages/pypi/src/managed_deepagents/_memory_instructions.py`:
`append_memory_instructions` no longer tags the memory block with
`cache_control` for `ChatAnthropic`.

Anthropic accepts four cache breakpoints per request and no `1h` one after a `5m`
one. Robin's prompt cache (`mda/rt/run_prompt.py`, `mda/rt/model_calls.py`) spends
all four, at `1h`, on the system prompt and the conversation. The memory block's own
`5m` breakpoint made five, so every Claude call of a run with memory mounted failed
with a 400. The block sits inside every authored middleware, so nothing in `mda/rt`
can retag it. Without its breakpoint the memory is cached with the conversation,
whose breakpoint follows it.

Upstream (langchain-ai/managed-deepagents-sdk#1340, fixed by its #1341): the memory block should fit the request's budget and the author's TTL
instead of always adding a default-TTL breakpoint, for example by adding one only
while fewer than four are set and with the TTL of the breakpoint before it, or by
letting the author turn it off in `define_memory`. Drop this change once MDA does;
`test_the_anthropic_model_the_backend_names_caches_for_an_hour` holds the budget.
