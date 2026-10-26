Display a self-contained HTML file from the sandbox in an isolated dashboard
iframe. Use this for visualizations, diagrams, interactive demos, SVG graphics, and small HTML
apps. Read the `html-artifacts` skill for the authoring rules: inline scripts, styles, Canvas,
WebGL, and data-URI assets all run, and omitting `<html>`/`<head>`/`<body>` wraps the content
in that skeleton with a minimal CSS reset. Relative paths are resolved from the sandbox
working directory. Honor system light/dark mode with `prefers-color-scheme`; ensure
readable colors in both modes rather than hard-coding a theme. Do not use this for
regular text responses or file operations.

Report errors from embedded JavaScript through the host's error-only bridge:
`parent.postMessage({ type: "openswe:client-error", version: 1, message: "Short error description" }, "*")`.
Install `error` and `unhandledrejection` listeners and call this for caught failures too.
Send only an error description (1–2000 characters), never secrets, user content,
request/response bodies, or URLs with query strings. The host validates the sending
iframe and forwards reports to its client-error pipeline; it grants no additional access.
