Say the work is blocked on the person or complete, together with the reply they get.

The person turned on **Goal mode**: your run keeps going until you call this, or start a wait that wakes the thread again, such as `schedule_thread_wakeup`.
`message` is your reply: on Slack this tool posts it as the final reply, so do not also call `slack_reply`; on the web, end the turn with it as your final response.

- `blocked`: you need something only they can provide; `message` asks for it.
- `complete`: the work is ready as the goal-mode guidance defines, or you answered what they asked; `message` is the outcome.
