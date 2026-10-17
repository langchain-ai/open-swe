Save a web app running in this thread's sandbox to the triggering user's **Apps** page, so they
can reopen it later from the dashboard without this thread.

Use it when the user asks to save, keep, or "make an app" they will come back to. For a one-off
preview, use `expose_port` instead.

Build the app so it survives on its own:

- Keep its code and data together in one directory outside any repository checkout, such as
  `/workspace/apps/<name>`, and pass that directory as `workdir`.
- Store data in a SQLite file inside `workdir` (for example `data.db`), never in memory or in
  `/tmp`. The sandbox keeps its filesystem while stopped, but every process dies when it stops.
- `start_command` runs from `workdir` through `sh -c` and must start the server in the
  foreground, listening on `0.0.0.0` at `port` (for example `python3 server.py` or
  `node server.js`). It must work in a fresh shell: install dependencies beforehand into
  `workdir`, and do not rely on environment variables set in this session.

Calling it starts the app with `start_command` unless something already answers on `port`, and
fails with the tail of the app's log when it does not come up within 60 seconds. Whenever the
user opens the app later, the dashboard wakes the sandbox and reruns `start_command` if the
server is down.

`name` uses lowercase letters, numbers, and single hyphens; saving the same name again replaces
that app. The returned `url` follows the same rules as an `expose_port` link: it is behind the
LangSmith login, and you cannot open it yourself. Share `url` and `apps_page` with the user.
