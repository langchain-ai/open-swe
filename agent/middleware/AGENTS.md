# AGENTS.md

Middleware here is spliced into the stack that `coding_agent/builder.py` owns, through the builder's `outer_middleware`, `tool_middleware`, `subagent_middleware`, `hook_middleware` and `late_hook_middleware` slots. Order within the stack is significant — pick the slot deliberately rather than appending to the end of one.
