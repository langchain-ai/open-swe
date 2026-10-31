Create a bearer download URL for one file in the active LangSmith sandbox.

Use this to share large binary artifacts such as videos, images, archives, or PDFs instead of pasting their contents into a response.
Anyone with the URL can download the file, so never use it for secrets or credentials.
Links do not expire by default; pass `expires_in_seconds` only when a link should stop working after a set time.
Set `content_disposition` to `inline` and provide an appropriate `content_type` when the browser should preview an image, video, or PDF.
If the returned `url` is a long signed URL rather than a short link, insert it programmatically from the JSON result and never retype it.
Don't create a link for an image `browser_screenshot` returned: the web transcript already shows it.
In GitHub PR descriptions, embed screenshots with explicit HTML `<img>` tags with an explicit `width` (for example, `width="640"`), including in Before/After tables; never use Markdown image syntax.
For PR images, read back the published body and rendered image URLs and verify anonymous GETs return HTTP 200 with the expected image MIME type after publication.
