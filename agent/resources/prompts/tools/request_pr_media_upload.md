Prepare a human-approved upload of a sandbox image or video to a GitHub pull request.

Pass the sandbox path of the media file and the pull request number. The tool records the exact
file bytes (SHA-256 digest) as a pending request and returns an approval URL. The upload only
executes after the thread owner approves that exact request in the dashboard; it runs server-side
with the approver's own GitHub OAuth token, so no credentials ever enter the sandbox. If the
upload's outcome is ambiguous it is marked failed rather than retried — ask the user instead of
requesting the same upload again. The media is posted as a new PR comment; existing PR bodies are
never modified.
