Read or set server-backed feature flags globally.
Requires a currently authorized workspace admin.
`read` requires a private admin surface; `set` also works in a verified sole-writer shared thread, returning only an acknowledgement.
Authorization is rechecked on every call.
Use only for an explicit request to change shared flags; if personal versus shared scope is unclear, ask first.

- `action`: `read` or `set`.
  In a sole-writer shared thread, set known flags directly without reading private settings first.
  If existing values are necessary, use a private dashboard thread or authenticated Slack DM.
- `flags`: for `set`, a nonempty mapping of requested names to true, false, or null.
  Omit to read.
  Null clears the stored override to the deployment default.
  Other settings remain unchanged.

Supported flags are the agent-manageable boolean fields in the global settings schema; use `read` on a private admin surface to discover their names and values.
“Allow Fable models” is `fable_enabled`.
`model_selection_enabled` controls the model picker in thread composers, off by default; it does not block requests for a model in conversation or other surfaces.
`cloud_first_desktop_enabled` is off by default.
It creates new desktop threads in cloud sandboxes and disables moves onto This Mac.
With an updated desktop release, private owner runs may offer individually approved local file and command tools alongside the cloud sandbox.
Existing local threads and CLI execution remain unchanged; enabling this flag never migrates or deletes files.
`implementation_walkthrough_enabled` is off by default.
When enabled, implementation threads use the existing planning tools to complete shared walkthroughs before delivering a PR, including drafts.
PR opens and head updates skip the background scout while a linked implementation thread is busy, including before its PR record is saved.
When the implementation thread is idle, head updates trigger automatic refreshes.
`concise_walkthrough_enabled` is off by default; when enabled, walkthrough plans and automated review finding descriptions for repositories use short bullet fragments.
Unknown fields, including credentials, model defaults, and approval automation, are rejected.
Fable's model safety policy applies when disabling its flag.
Approval modes are managed per repository by `manage_review_approval_mode`; set `approve` only when explicitly requested.
Personal flags are set with `save_user_settings`; browser-local flags cannot be changed by this tool.
