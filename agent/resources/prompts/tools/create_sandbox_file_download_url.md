Create a temporary, sandbox-scoped bearer download URL for one file in the active LangSmith sandbox.

Use this to share large binary artifacts such as videos, images, archives, or PDFs instead of
pasting their contents into an ephemeral chat response. Anyone with the URL can download the file,
so never use it for secrets or credentials. The URL stops resolving when the sandbox is reclaimed;
never embed it in a pull request, issue comment, or other durable document. Set `content_disposition`
to `inline` and provide an appropriate `content_type` when the browser should preview an image, video,
or PDF.
