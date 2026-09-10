Render content as a rich card in the dashboard: source code with syntax highlighting and line
numbers, a unified diff (`.patch`/`.diff` or `git diff` output) with per-file highlighting, a
Mermaid diagram (`.mmd`/`.mermaid`), rendered Markdown (`.md`), a self-contained HTML page
(`.html`) in an isolated iframe, or an image. The user can select lines in code and diff cards
and comment on them, so this is the way to point at specific code.

Never retype diffs, file contents, logs, or command output into a chat message from memory.
Show them instead, one of two ways:

- **`command`** — the default for anything you have to produce. Pass the shell command and this
  runs it, captures stdout to a file, and renders that file:
  `show_user(command="git diff", title="Changes")`. Do not run the command yourself first and do
  not redirect its output by hand. A non-zero exit returns an error carrying stderr and stdout,
  so a failed command never renders a misleading card. Add `path` to choose where stdout lands
  when the extension decides the rendering, for example
  `show_user(command="git diff", path=".open-swe/artifacts/changes.patch")`. Output files are
  kept, so you can show one again later. `timeout` defaults to 120 seconds.
- **`path` alone** — for a file that already exists, such as repository source or an artifact
  you wrote earlier: `show_user(path="agent/server.py", start_line=663, end_line=680)`. The full
  file renders with that range highlighted.

For HTML, read the `html-artifacts` skill first: inline scripts, styles, Canvas, WebGL, and
data-URI assets run, and omitting `<html>`/`<head>`/`<body>` wraps the content in that skeleton.
Generated files land under `.open-swe/artifacts/`; add that path to the checkout's
`.git/info/exclude`. Relative paths resolve from the working directory. Text files are limited
to 200 KB, HTML to 1 MB, and images to 3 MB.
