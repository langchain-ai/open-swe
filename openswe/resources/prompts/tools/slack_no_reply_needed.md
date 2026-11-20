Declare that this Slack turn needs no message sent to the person who asked.
Use this only when the current Source Context routes the response to Slack, not when the response channel is web.
This tool's presence does not mean the current run may use it: its schema stays visible across response surfaces for prompt caching.
On the web, answer through a normal assistant message, or end without text when silence is warranted; do not call this tool.
The following instructions apply only to Slack turns.
After this call succeeds, end the turn; do not call it again.

Only for a turn that genuinely warrants silence: you were mentioned in passing, another participant already answered, you were asked to stand by, you consumed an internal worker update with nothing useful to report to the user yet, or `switch_to_performance_model` already confirmed a switch-only request privately.
Anything the asker needs now — an answer, a question, a requested status, a refusal — goes through `slack_reply` instead, and staying silent there loses it entirely.

`reason` is one short sentence, read only by the operators of this agent.

`confirmation` must be typed out exactly as: "The user cannot see anything I do not send to Slack."
Your thinking, tool results, and plain assistant messages never reach them.
