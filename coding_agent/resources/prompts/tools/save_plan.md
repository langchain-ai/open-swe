Publish a self-contained HTML plan artifact from the sandbox.

Use this in plan mode once the artifact is ready. Outside plan mode, use it
to share a long response without switching the thread into plan mode. Write
one ``.html`` file directly under ``/workspace/plans/`` and pass that path
here. Read the ``html-artifacts`` skill for the authoring rules: write the
page content and omit ``<html>``/``<head>``/``<body>`` — they are added
here, along with a minimal CSS reset — and include a ``<title>``. The
artifact is rendered in an opaque-origin sandboxed iframe under a strict CSP:
inline CSS and JavaScript, Canvas, WebGL, and Google Fonts work; network
access and web storage do not.

Args:
    plan_file_path: Path to the HTML artifact in the sandbox.

Returns:
    ``{success: True, path}`` on success, or ``{success: False, error}``.
