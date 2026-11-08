Move your goal from the state you last saw it in.

Until the goal is complete, your run keeps going until the goal waits on the person, pauses or completes, or until something will wake the thread again, such as `listen_events`, `monitor` or `schedule_thread_wakeup`.

- expected_state: The state you last saw the goal in, from `goal_get` or an earlier call; while you work it is `working`.
  The goal moves only if it is still in that state; otherwise nothing changes and the result shows the goal as it is now.
- state:
  - `waiting_on_user` with `reason`: `blocked` when you need something only they can provide, `ready` when the work is ready for them to act on, such as a reviewed draft PR for them to mark ready.
  - `complete` when the objective is done and nothing is left for you to do, such as a question you answered or a request you finished.
    If the goal was explicitly set, or you edited files or opened a pull request for it, the first call asks you to check your work against the objective; nothing moves until you call it again.
  - `paused`, only when the person asks you to stop working toward the goal; their next message, or `working`, resumes it.
  - `working`, only when the person asks you to resume a paused goal (`expected_state="paused"`).

This says nothing to the person.
Tell them what you need, what is ready or what you finished in your reply, as on any other turn: on Slack with `slack_reply` and `response_type="final"`, on the web as your final response.
