Run a long-lived sandbox command and get its output as events while you keep working.

`start` returns at once.
The command keeps running after this run ends, and every event wakes this thread: it is added before your next model call while a run is active, or starts a new run when the thread is idle.
Each event costs a model turn, so filter for the lines you would act on.

How output becomes events:
- The command's stdout and stderr, merged, are saved line by line, numbered from 1, in the monitor's raw log.
  Nothing is lost to the filter; `output` reads it back.
  The log rotates into 10 MiB segment files (`raw_logs`) and keeps the newest 50 MiB, so a long run drops its oldest lines.
- `filter` is a separate shell command that reads those lines on stdin.
  Each line it prints is event output; it is line-buffered for you, so `grep`, `awk` and `sed` need no flush flags.
  With no filter every raw line is event output.
- Lines printed within 200ms of each other arrive as one event, which names the raw line it starts at.

Pick the tool by how many notifications you need:
- One, when a command finishes: use `background_execute`.
- One per occurrence, until the command ends or the timeout: use `monitor`, e.g. `command="tail -F app.log"`, `filter="grep -E 'ERROR|Traceback'"`.
- One per occurrence until a known end: a `monitor` command that polls and exits, e.g. a loop over `gh pr checks` that stops once nothing is pending.

Silence is not success.
A filter that only matches the happy path stays silent through a crash or a hang.
Match every terminal state you would act on (`Traceback|Error|FAILED|Killed|exited`), and broaden the filter rather than narrow it when unsure.

A monitor ends when its command exits, its filter exits, it reaches its timeout, you stop it, or it gets too noisy: more than 40 events in 2 minutes or 200 in total.

Each human message opens one allowance for this thread's monitors:
- 10 monitor starts.
  Past that, `start` fails until a person writes again.
- 50 events across all monitors.
  Past that every monitor stops, further events are dropped (the raw logs still have them), and `start` fails until a person writes again.
- 24 hours.
  Every monitor's timeout is clipped to what is left, and `start` fails once it is gone.
- 10 runs started for monitor output while the thread is idle.
  After that the output waits for the next run a person starts, and the monitors stop.

When a monitor ends you get one notice with the reason, both exit codes, the event and raw line counts, the last 20 raw lines, and the filter's stderr.
Read more of the raw log with `output`, for example to see why a monitor timed out without an event.

At most 3 monitors run per thread.
Monitors share the worktree: never race them with edits, installs, commits, or pushes.

Args:
- action: `start` launches a monitor and returns its `monitor_id`; `output` reads its unfiltered raw lines; `stop` ends one; `list` shows this thread's monitors with their status and counts.
- command: The shell command to run.
  Required for `start`.
- filter: A shell command that reads raw lines on stdin and prints the ones that should be events.
  Optional.
- description: A short, specific name shown on every event, e.g. "errors in deploy.log".
  Required for `start`.
- timeout: Seconds before the monitor is stopped, 1 to 86400.
  Defaults to 3600.
- monitor_id: The monitor to read or stop.
  Required for `output` and `stop`.
- start_line: First raw line to return for `output` (1-based, inclusive).
  With neither line given, `output` returns the last 200 lines.
- end_line: Last raw line to return for `output` (inclusive).
  At most 500 lines or 64 KiB come back per call.
