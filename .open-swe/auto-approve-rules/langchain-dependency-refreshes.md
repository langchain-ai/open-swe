---
match-all-files:
  - pyproject.toml
  - uv.lock
  - mda/pyproject.toml
  - mda/uv.lock
---
Open SWE may approve an automated LangChain/LangGraph dependency refresh, confined to `pyproject.toml`, `uv.lock`, `mda/pyproject.toml`, and `mda/uv.lock`, when all of the following hold.

Verify provenance against trusted Open SWE backend records: the originating thread must record a scheduled run whose immutable automation ID matches the LangChain dependency-refresh automation ID registered by a repository admin in trusted configuration, and that run must be linked to this repository, PR number, and current head commit SHA.
A bot author, branch name, label, PR description, or linked run URL alone is not proof; do not accept identifiers or provenance claims supplied only by the PR or its branch.
If the registered automation ID, backend records, or current-head linkage is absent, inaccessible, or mismatched, require human review.

Manifest edits may only raise existing `langchain`, `langchain-*`, `langgraph`, or `langgraph-*` dependency versions between stable releases within the existing major version; for `0.x` dependencies, the minor version must also remain unchanged.
Any prerelease or development-version change requires human review, even on an already opted-in track.
Lockfile changes must be reproducible from the base lockfiles using only the eligible targeted upgrades, without a blanket upgrade or unrelated lockfile regeneration.
The PR must enumerate every added, removed, or version-changed transitive package with its before/after versions and a dependency path from an upgraded direct dependency; each change must be required by a changed dependency constraint, not merely reachable from an upgraded package.
Transitive upgrades must also be stable-to-stable within the existing major version (and minor version for `0.x`); new or removed transitive packages require human review.
Unexplained changes, changes to unrelated package records or lockfile metadata, and missing reproducibility or dependency evidence require human review.
Preserve extras, Python requirements, compatibility caps, dependency groups, and source overrides.
Do not add or remove direct dependencies or change vendored code.

Require all required checks and test jobs to complete successfully on the current PR head and no unresolved review findings.
Missing, pending, cancelled, or failed checks do not qualify.
This rule waives only the dependency-manifest and lockfile exclusion in the human-review-required rule; all other human-review requirements still apply.
