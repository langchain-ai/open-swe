### Large File Sharing

Generated presentation artifacts are temporary delivery output, not repository assets. Never add
screenshots, videos, generated HTML, or other presentation files to `artifacts/` or another path in
the target repository unless the user explicitly asks for a durable repository asset or test
fixture. When a publishing tool requires files inside the sandbox work directory, keep temporary
files under `.open-swe/artifacts/` and add that path to the checkout's local `.git/info/exclude`.

Prefer `output_iframe` for HTML previews. In Slack, use `slack_attach_file` for small HTML, PNG,
JPG/JPEG, GIF, WebP, or PDF attachments; keep `slack_attach_html` for compatibility when an HTML
attachment is specifically requested. Use `create_sandbox_file_download_url` only for files over
the 10 MB Slack attachment limit, non-Slack surfaces, or unsupported types, and set
`content_disposition="inline"` with the appropriate `content_type` when the browser should preview
the file. Never create download links or attachments for secrets or credentials. Take a screenshot
for applicable UI-facing changes and share it with the user in the final delivery without committing it.
