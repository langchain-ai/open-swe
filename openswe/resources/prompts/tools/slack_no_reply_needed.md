Declare that this turn needs no message sent to the person who asked.

Only for a turn that genuinely warrants silence: you were mentioned in passing,
another participant already answered, you were asked to stand by, or you consumed
an internal worker update with nothing useful to report to the user yet. Anything
the asker needs now — an answer, a question, a requested status, a refusal — goes
through `slack_reply` instead, and staying silent there loses it entirely.

`reason` is one short sentence, read only by the operators of this agent.

`confirmation` must be typed out exactly as:
"The user cannot see anything I do not send to Slack."
Your thinking, tool results, and plain assistant messages never reach them.
