Policies are scoped to the current workspace and require a repository bound there; there is no instance-wide fallback. Admin-only per-repository workflow policy. Use only when an admin explicitly asks to configure workflow dispatch. Read requires a private admin surface; set/remove also support verified sole-writer shared threads with acknowledgement-only results. Repository access and current admin authorization are checked on every call.

Set requires a workflow filename, a fixed ref (prefer a protected branch), optional fixed_inputs, and allow_bot_inputs (false by default). Bot inputs may supplement but never override fixed keys. Remove revokes dispatch immediately; read returns one workflow's policy. Do not store credentials in inputs. Keep potentially dangerous inputs fixed and use GitHub environment protection for deployment approvals. The GitHub App needs Actions: write for this feature; normal installation tokens cap Actions at read-only and never inherit Actions: write.

Admins can also configure policies under Workspace settings → GitHub Workflows. The optional workspace argument selects which workspace to manage; dispatch always uses its current workspace.

The admin_only policy restricts dispatch to an authorized admin in an admin thread, checked at dispatch time; ordinary threads cannot trigger it even when their requester is an admin.
