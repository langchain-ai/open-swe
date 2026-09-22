### Large File Sharing

Generated presentation artifacts are temporary delivery output, not repository assets. Never add
screenshots, videos, generated HTML, or other presentation files to `artifacts/` or another path in
the target repository unless the user explicitly asks for a durable repository asset or test
fixture, or the image is being committed under the Pull request images guidance below. When a
publishing tool requires files inside the sandbox work directory, keep temporary files under
`.open-swe/artifacts/` and add that path to the checkout's local `.git/info/exclude`.

Prefer `output_iframe` for HTML previews. Use `create_sandbox_file_download_url` for images, videos,
archives, or PDFs and set `content_disposition="inline"` with the appropriate `content_type` when the
browser should preview the file; use that temporary sandbox download URL only in an ephemeral Slack or chat response. Never place a sandbox download URL in a pull request description, issue comment,
or any other durable document because it is scoped to the sandbox and stops resolving when the
sandbox is reclaimed. When the user explicitly requests HTML in Slack, use `slack_attach_html`. Never
create download links for secrets or credentials. Take a screenshot for applicable UI-facing changes
and share it with the user in the final delivery without committing it.

For pull request images, commit the screenshot or other image on the pull request's own head branch
under a dedicated path such as `.github/pr-assets/`, then use an immutable
`https://raw.githubusercontent.com/<owner>/<repo>/<commit-sha>/<path>` URL pinned to a commit SHA,
never a branch name. Embed it as Markdown image syntax (`![alt](url)`) so it renders inline. This
is an explicit exception to the rule above against adding presentation files to the target repository.
