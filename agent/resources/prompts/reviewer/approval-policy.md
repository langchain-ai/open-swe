+++
max_risk_score = 2
minimum_confidence = "high"
required_checks = []
human_review_paths = [".github/*", "CODEOWNERS", "*/CODEOWNERS"]
+++
# Default approval policy

## Bounded change
The whole PR has a narrow, well-understood effect and limited consequences if
wrong. Explain the changed behavior and affected callers. Diff size and an
absence of findings alone are insufficient evidence.

## Verification
Relevant behavior is covered by verification you actually inspected. Cite the
tests, checks, or concrete reasoning that cover the changed behavior. Do not
claim a test passed based only on its existence. Missing required context or
unreviewed files means unknown, not pass.

## Human judgment
The change does not require a human decision about authorization or security,
data migration or deletion, dependencies, deployment, public contracts, or
product intent. Check repository ownership requirements and outstanding review
discussions. If a required owner must review or a discussion needs human
resolution, fail this criterion. If those requirements cannot be established,
mark it unknown.
