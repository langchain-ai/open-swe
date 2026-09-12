### Large File Sharing

Generated presentation artifacts are temporary delivery output, not repository assets. Never add
screenshots, videos, generated HTML, or other presentation files to `artifacts/` or another path in
the target repository unless the user explicitly asks for a durable repository asset or test
fixture. Keep them under `/artifacts/`, outside the sandbox work directory, unless the publishing
tool requires a work-directory source.

Prefer `output_iframe` for HTML previews. Use `create_sandbox_file_download_url` for images, videos,
archives, or PDFs and set `content_disposition="inline"` with the appropriate `content_type` when the
browser should preview the file; link or embed that URL in the final response or pull request. The
file must live under `/artifacts/`; the tool rejects every other location. When the user explicitly
requests HTML in Slack, use `slack_attach_html`. `output_iframe` and `slack_attach_html` read their
source from the sandbox work directory, so keep their inputs under `.open-swe/artifacts/` and add
that path to the checkout's local `.git/info/exclude`. Never create download links for secrets or
credentials. Take a screenshot for applicable UI-facing changes and share it with the user in the
final delivery without committing it.
