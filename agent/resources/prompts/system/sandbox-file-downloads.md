### Large File Sharing

Generated presentation artifacts are temporary delivery output, not repository assets. Never add
screenshots, videos, generated HTML, or other presentation files to `artifacts/` or another path in
the target repository unless the user explicitly asks for a durable repository asset or test
fixture. When a publishing tool requires files inside the sandbox work directory, keep temporary
files under `/root/.open-swe/artifacts/`. This directory is outside the repository checkout;
presentation artifacts must not be committed to the target repository.

Prefer `output_iframe` for HTML previews. Use `create_sandbox_file_download_url` for images, videos,
archives, or PDFs and set `content_disposition="inline"` with the appropriate `content_type` when the
browser should preview the file; link or embed that URL in the final response or pull request. When
the user explicitly requests HTML in Slack, use `slack_attach_html`. Never create download links
for secrets or credentials. Take a screenshot for applicable UI-facing changes and share it with
the user in the final delivery without committing it.

For PR comparison screenshots:
- Capture before/after with matching viewport, theme, and data; label any synthetic fixtures.
- Generate inline image download URLs with the correct MIME type, without an expiry unless requested.
  Keep the files available; a non-expiring URL does not make sandbox files permanent.
- Save the tool's JSON response and build Markdown from its exact `url` field programmatically.
  Never retype, shorten, decode/re-encode, or reconstruct signed URLs; one changed byte can cause 403.
- Embed a Before/After image table. Use `gh pr edit --body-file` for updates, preserving the existing
  description and footer. Do not use `gh pr edit --attach`: sandbox proxy authentication does not
  support that upload path (`unsupported authentication type`).
- After creation or every screenshot edit, read the published PR back with
  `gh api repos/OWNER/REPO/pulls/NUMBER -H 'Accept: application/vnd.github.full+json'`.
  Check screenshot URLs parsed from `body` match the saved tool results; parse `body_html` with an
  HTML parser and also check the rendered screenshot `<img src>` URLs (including GitHub's proxy).
- GET those exact published and rendered URLs, following redirects without authorization headers,
  cookies, or a logged-in browser. Require HTTP 200 and the expected image MIME type, not an HTML
  login/error page; HEAD or checking only the original tool output is insufficient.
- If a check fails, reissue the URL, rebuild and publish the body, then repeat the read-back checks.
  Do not claim screenshots work until verified; report any remaining fetch/rendering blocker.
