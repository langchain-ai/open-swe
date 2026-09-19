Read, save, or reset Open SWE's shared or repository approval policy. Available only to an authenticated admin in their private admin thread.

Omit `repository` for shared defaults, or pass `owner/repo` for a repository override. Always read first and pass the returned `effective_version` as `expected_version` on a save or reset. If the version changed, read again and reconcile the user's intended changes; do not overwrite a concurrent edit blindly.

`policy` contains structured `rules` (maximum risk score 1–5, minimum confidence, required check names, human-review path globs) and `criteria_markdown` with named `##` sections. A repository policy replaces the shared policy in full and may be more or less restrictive. Empty repository check/path lists or criteria remain empty; they do not inherit shared values. When creating an override, start from the shared policy and apply the user's requested changes so other values are preserved unless the user asks to replace them.

Reset removes customization: a repository inherits shared policy; shared defaults return to the built-in policy. Policy changes affect future evaluations, preserve past assessments, and do not enable actual GitHub approvals. Learned review style and usefulness feedback are separate from approval policy.
