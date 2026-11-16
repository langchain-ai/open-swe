Publish a self-contained HTML artifact from the sandbox.

Use this when the user asks for an artifact or HTML, or when interactive or visual content materially benefits from a standalone page.
Default to plain text or Markdown in the conversation for answers, drafts, plans, reports, and PR analysis; length alone is not a reason to publish an artifact, and requests for in-thread text take precedence.
Write one ``.html`` file directly under ``/workspace/plans/`` and pass that path here.
Read the ``html-artifacts`` skill for the authoring rules: write the page content and omit ``<html>``/``<head>``/``<body>`` — they are added here, along with a minimal CSS reset — and include a ``<title>``.
The artifact is rendered in an opaque-origin sandboxed iframe under a strict CSP: inline CSS and JavaScript, Canvas, WebGL, and Google Fonts work; network access and web storage do not.
Honor system light/dark mode with `prefers-color-scheme`; ensure readable colors in both modes rather than hard-coding a theme.

Args:
- artifact_file_path: Path to the HTML artifact in the sandbox.

Returns: ``{success: True, path}`` on success, or ``{success: False, error}``.
