Publish a self-contained HTML artifact from the sandbox.

Use this to share a plan, report, or long response as an ordinary artifact. Write
one ``.html`` file directly under ``/workspace/plans/`` and pass that path
here. Read the ``html-artifacts`` skill for the authoring rules: write the
page content and omit ``<html>``/``<head>``/``<body>`` — they are added
here, along with a minimal CSS reset — and include a ``<title>``. The
artifact is rendered in an opaque-origin sandboxed iframe under a strict CSP:
inline CSS and JavaScript, Canvas, WebGL, and Google Fonts work; network
access and web storage do not. Honor system light/dark mode with
`prefers-color-scheme`; ensure readable colors in both modes rather than hard-coding a theme.

For artifacts with JavaScript, install `error` and `unhandledrejection` listeners
and report caught failures too using the error-only host bridge:
`parent.postMessage({ type: "openswe:client-error", version: 1, message: "Short error description" }, "*")`.
Send only an error description (1–2000 characters), never secrets, user content,
request/response bodies, or URLs with query strings. The host validates the sending
iframe and forwards reports to its client-error pipeline; it grants no additional access.

Args:
    artifact_file_path: Path to the HTML artifact in the sandbox.

Returns:
    ``{success: True, path}`` on success, or ``{success: False, error}``.
