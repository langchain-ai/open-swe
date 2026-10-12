Create a bearer download URL for one file in the active LangSmith sandbox.

Use this to share large binary artifacts such as videos, images, archives, or PDFs instead of
pasting their contents into a response. Anyone with the URL can download the file, so never use
it for secrets or credentials. Links do not expire by default; pass `expires_in_seconds` only
when a link should stop working after a set time. Set `content_disposition` to `inline` and
provide an appropriate `content_type` when the browser should preview an image, video, or PDF.
Preserve the returned `url` byte-for-byte: save the JSON result and insert its `url` programmatically,
never reconstruct a signed URL. For PR images, read back the published body and rendered image URLs
and verify anonymous GETs return HTTP 200 with the expected image MIME type after publication.
