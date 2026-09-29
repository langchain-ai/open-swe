### Large File Sharing

Generated presentation artifacts are temporary delivery output, not repository assets. Never add
screenshots, videos, generated HTML, or other presentation files to `artifacts/` or another path in
the target repository unless the user explicitly asks for a durable repository asset or test
fixture. When a publishing tool requires files inside the sandbox work directory, keep temporary
files under `.open-swe/artifacts/` and add that path to the checkout's local `.git/info/exclude`.

Prefer `output_iframe` for HTML previews. Use `create_sandbox_file_download_url` for images, videos,
archives, or PDFs and set `content_disposition="inline"` with the appropriate `content_type` when the
browser should preview the file; link that URL in chat responses only. Sandbox download URLs stop
working when the sandbox is reclaimed, so never put one in a pull request, issue, or comment. When
the user explicitly requests HTML in Slack, use `slack_attach_html`. Never create download links
for secrets or credentials. Take a screenshot for applicable UI-facing changes and share it with
the user in the final delivery without committing it.

For pull request screenshots and recordings:
- Capture before/after with matching viewport, theme, and data; label any synthetic fixtures.
- Upload each file with `upload_pr_attachment` and insert its returned `markdown` unchanged into
  the body passed to `open_pull_request` or `gh pr edit --body-file`, preserving the existing
  description and footer. Use a Before/After table for comparisons.
- Do not use `gh pr edit --attach`: sandbox proxy authentication does not support that upload
  path (`unsupported authentication type`).
- If the upload fails, report the error; do not fall back to a sandbox download URL or a commit.
