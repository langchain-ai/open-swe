Update the current user's preferences. Available only in their private threads; never accepts another user's identity.

Pass `settings` containing only the fields the user requested to change. Unspecified preferences are preserved. Use `category="profile"` for default_model, reasoning_effort, default_subagent_model, subagent_reasoning_effort, default_repo, base_branch, branch_prefix, auto_fix_ci, model_routing_enabled, dm_session_enabled, draft_prs, and review_draft_prs. Use `category="dashboard"` for default_visibility (public/private), local_tracing_project, and default_workspace.

Changes apply to subsequent runs, not an already-running agent. Model changes must include a supported model/effort pair. This tool cannot change credentials, identity, or admin/workspace settings.
