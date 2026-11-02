Refused: this command waits with `sleep`, which holds the run and burns its time while nothing happens.
Wait with a tool that frees the run and wakes this thread when there is something to see:
- `monitor` with `interval` to wait for a condition: it runs your check command every `interval` seconds and wakes the thread once, with the check's output, when the check exits 0.
  For example, a server up: `command="curl -fsS localhost:3000/health"`, `interval=5`.
- `monitor` without `interval` to react to output as a command prints it, such as a log or a dev server.
- `listen_events` to wait for a GitHub or Slack event, such as `check_suite.completed` for CI on your PR.
- `schedule_thread_wakeup` to look again after a fixed delay.
Then end the turn.
