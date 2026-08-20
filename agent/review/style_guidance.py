"""Reviewer themes to steer repository style analysis."""

REVIEWER_STYLE_THEMES = """
The downstream reviewer agent looks for high-signal, diff-anchored defects — not nits.
When learning this repo's style, note how human reviewers align (or don't) with:

**Usually flag:** correctness regressions, wrong operators/variables, async footguns,
read-modify-write races, API/signature drift, nil/None deref, security boundaries (SSRF,
auth/cache asymmetry), broken tests, migration/ORM bypass, template/React contract breaks.

**Usually skip:** rename/style preferences, speculative "might break someday", scope-policing,
pre-existing issues, duplicate findings for the same bug across files, generic perf opinions.

**Severity:** tie labels to user-visible/runtime consequence, not taste.

**Tone:** prefer direct, concrete failure modes; cite the line; suggest only tiny obvious fixes.
"""
