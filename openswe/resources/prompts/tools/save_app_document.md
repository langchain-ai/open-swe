Save a document, with files from the sandbox as its attachments, to an app this review works for.

Writes go to the app's storage shared for this pull request's repository, only into collections the app's review step names, and only under keys starting `pulls/<this pull request's number>/`.
A key ending in `/` gets a time-ordered id appended, so the newest of a prefix reads first; the result returns the full key.
Saving the same key again replaces the document and any file of the same name.

Args:
- app: The app's id, as this review's instructions give it.
- collection: One of the collections the app's review step names.
- key: Where the document goes, starting `pulls/<number>/`.
- document: The JSON object the app's instructions describe, at most 256 kB.
- files: Optional attachments, as `{name: path}`: each name of letters, digits, `.`, `_` and `-`, and each path an absolute path under `/workspace/` of a PNG, JPEG, WebP or GIF image, or a WebM or MP4 video, at most 8 MiB.
  At most 40.

Returns: `{success: True, key}`, or `{success: False, error}`.
