Read or set the shadow review experiment at the instance level or for a workspace.
Requires a currently authorized workspace admin.
`read` requires a private admin surface; `set` also works in a verified sole-writer shared thread, returning only an acknowledgement.
Authorization is rechecked on every call.
Use only for an explicit request to change the experiment.

A shadow review is a second reviewer on another model.
When a pull request's first review starts, `percent` of pull requests are sampled; each sampled pull request's shadow then follows every review of it, first reviews and re-reviews alike, with the same prompt and context.
Shadows never post to GitHub or Slack: their findings and what each review would have posted are recorded in the `review_shadow` table for offline comparison with the pull request's own findings.
A sampled pull request keeps the model it was sampled with; changing the model affects only pull requests sampled afterwards.

- `action`: `read` or `set`.
- `settings`: for `set`, any of:
  - `percent`: 0 to 100; 0 turns sampling off.
    Pull requests already sampled keep their shadow.
  - `model` and `reasoning_effort`: the shadow's model and effort, set together.
    Sampling stays off until both are set.

  A field passed as null clears the instance value, or restores inheritance from the instance for a workspace.
  Omit `settings` to read.
- `workspace`: optional slug.
  Omit to affect the instance and all inheriting workspaces.
  A workspace must already exist.
