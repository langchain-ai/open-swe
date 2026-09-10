Render a file from the working directory as a rich card in the dashboard:
source code with syntax highlighting and line numbers, a unified diff (`.patch`/`.diff` or
`git diff` output) with per-file highlighting, a Mermaid diagram (`.mmd`/`.mermaid`), rendered
Markdown (`.md`), a self-contained HTML page (`.html`) in an isolated iframe, or an image. The
user can select lines in code and diff cards and comment on them, so this is the way to point
at specific code.

Never retype diffs, file contents, logs, or generated output into a chat message from memory.
Write them to a file and show that instead, for example
`git diff > .open-swe/artifacts/changes.patch` followed by
`show_file(path=".open-swe/artifacts/changes.patch", title="Changes")`. To point at existing
source, pass its path with `start_line`/`end_line`; the full file is rendered with that range
highlighted. For HTML, read the `html-artifacts` skill first: inline scripts, styles, Canvas,
WebGL, and data-URI assets run, and omitting `<html>`/`<head>`/`<body>` wraps the content in
that skeleton. Keep temporary files under `.open-swe/artifacts/` and add that path to the
checkout's `.git/info/exclude`. Relative paths resolve from the working directory. Text files
are limited to 200 KB, HTML to 1 MB, and images to 3 MB.
