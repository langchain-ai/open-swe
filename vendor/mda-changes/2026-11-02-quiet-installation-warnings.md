# Upstream as-is.

**What:** Split pending GitHub App installation requests and unaccepted installation permissions from App-level warnings in `crates/mda/src/deploy/github_channel/report.rs`. `github_channel/mod.rs` shows those installation warnings only in an interactive terminal.

**Why:** Open SWE's public GitHub App has many unrelated installations. Their owners' pending approvals flood unattended deploy logs without blocking deployment. App configuration, missing-installation, suspension, repository-coverage, and token warnings remain visible.

**Upstream:** Drop this change once MDA keeps installation approval diagnostics interactive while retaining actionable deployment warnings in unattended runs.
