---
name: docs-review
description: Review linked documentation PRs for accuracy and coverage against a source change; report corrections without editing.
---

Read the source diff and the full relevant implementation. Identify externally
observable changes, changed APIs, defaults, configuration, migration steps and UI.
For each linked docs checkout, inspect its base-to-head diff, the resulting pages,
and relevant currently published docs through the docs MCP tools when configured.
Consider the linked PRs together; documentation may be split across several PRs.
A draft docs PR is valid review input. Flag a closed unmerged or stale PR when it
fails to provide the necessary documentation. Do not assume a merged PR is wrong:
check whether the docs base already incorporates its accurate content.

Check factual accuracy, completeness, examples, navigation, writing conventions,
and screenshots when the source change alters a documented UI flow. Use applicable
trusted-base AGENTS.md instructions and referenced skills. Report actionable gaps
with the relevant docs file/section and the verified user-facing behavior. State
uncertainties rather than making up requirements.

Do not edit, push or author a replacement PR while any docs PR is linked. This
includes agent-authored docs PRs. Call finish_docs_review with docs_needed=true and
findings assigned to the affected linked docs PR numbers if corrections are needed.
The tool posts comments on those docs PRs and links the findings from the source PR.
If the docs are accurate and sufficient, finish with docs_needed=false and no
findings. Never demand docs merely because a PR exists: internal refactors, tests,
and implementation-only changes may require no user-facing documentation.
