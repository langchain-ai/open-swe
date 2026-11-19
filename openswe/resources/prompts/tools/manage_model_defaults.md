Read or set Robin-wide default models and reasoning efforts under **Model Defaults**.
Requires a currently authorized workspace admin.
`read` requires a private admin surface; `set` also works in a verified sole-writer shared thread, returning only an acknowledgement.
Authorization is rechecked on every call.
Use only for an explicit request to inspect or change default role models.

- `action`: `read` or `set`.
- `role`: for `set`, one of `agent`, `agent_subagent`, `thread_title`, `reviewer`, `reviewer_subagent`, or `review_scout`.
  Omit for `read`.
- `model` and `reasoning_effort`: for `set`, the supported model ID and reasoning effort to save together, or omit/pass both as null to reset that role to its built-in default.
  Omit for `read`.
