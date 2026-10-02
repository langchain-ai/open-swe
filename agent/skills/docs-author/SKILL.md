---
name: docs-author
description: Determine whether a source PR needs documentation and author a minimal docs PR when no docs PR is linked.
---

Inspect the source base-to-head diff and relevant full code. Identify changes to
supported behavior, APIs, configuration, installation, defaults, migrations and
user-visible UI. Compare with the docs repository and currently published docs
using the docs MCP when configured. Internal refactors, tests and implementation
changes without externally observable effects generally need no documentation.

If no docs update is needed, call finish_docs_review with docs_needed=false,
findings=[], and a short evidence-based summary. If uncertain because evidence is
missing, report the uncertainty explicitly; do not claim docs are accurate.

If documentation is needed, edit the docs authoring checkout. Follow every
applicable AGENTS.md from the trusted docs base, including for new files. Read
referenced writing skills, reuse nearby patterns, keep examples accurate, and
make the smallest complete change. Update navigation when adding pages. Use
screenshots for changed UI flows where useful. Run trusted docs-base lint/build
commands when available; do not execute source PR scripts or install packages.

Review the resulting diff against the source changes, check links and examples,
and validate the docs. Never include private source repository URLs, internal
paths, secrets or proprietary code in public outputs. Put validation and any
limitations in the PR description. Call publish_docs_pr with a clear title and
body. The tool creates a draft docs PR and links it in the source PR comments.
It rechecks source eligibility and links before publishing; if a docs PR was linked
in the meantime, stop authoring and let the new review run handle it.
