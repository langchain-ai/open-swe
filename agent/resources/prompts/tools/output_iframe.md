Display a self-contained HTML file from the sandbox in an isolated dashboard
iframe. Use this for visualizations, diagrams, interactive demos, SVG graphics, and small HTML
apps. Read the `html-artifacts` skill for the authoring rules: inline scripts, styles, Canvas,
WebGL, and data-URI assets all run, and omitting `<html>`/`<head>`/`<body>` wraps the content
in that skeleton with a minimal CSS reset. Relative paths are resolved from the sandbox
working directory. Do not use this for regular text responses or file operations.
