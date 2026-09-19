# Approval policy evaluation

Open SWE evaluates whether a reviewed PR could be approved under a versioned
policy. It publishes **Would approve**, **Needs human review**, or **Insufficient
evidence** alongside the PR risk score. This is a shadow evaluation: GitHub
reviews remain comments, and Open SWE does not grant approval or merge the PR.

The default policy allows risk at most 2/5 with high confidence and requires a
bounded change, relevant verification, and no outstanding need for human
judgment. Every required criterion must pass. A known failure routes to human
review; otherwise, missing evidence produces an insufficient-evidence result.

## Repository policy

Place `APPROVAL_POLICY.md` at the repository root to replace the default policy.
Open SWE reads it from the PR's **base commit**, never from the proposed head.
This first version supports one root policy, not directory inheritance.

Optional TOML frontmatter sets machine rules; Markdown level-two headings define
the criteria the reviewer must assess:

```markdown
+++
max_risk_score = 2
minimum_confidence = "high"
required_checks = ["tests", "typecheck"]
human_review_paths = ["auth/*", "migrations/*", ".github/*"]
+++
# Approval policy

## Limited impact
The change has a narrow, well-understood effect. Explain affected behavior and callers.

## Verification
Cite inspected verification of the changed behavior. Missing context means unknown.

## Owner judgment
Changes to public contracts or security boundaries need a human owner.
```

Omitted rules use the default maximum risk of 2 and minimum confidence of high;
check-name and path lists default to empty. Requirements before the first
level-two heading are also evaluated. Policies must have 1–20 uniquely named
criteria and fit within 24 KB. Invalid or unreadable policies produce unknown
eligibility rather than falling back to a default. A missing policy uses the
built-in default only after Open SWE successfully reads the base directory.

Path patterns use case-sensitive shell wildcard matching over the full
repository-relative path; `*` matches slashes. Renames inspect both old and new
paths. Changing any `APPROVAL_POLICY.md` always requires human review, regardless
of the path rules.

## Evidence and decisions

The reviewer calls `get_review_approval_policy`, inspects the entire PR, and
reports pass/fail/unknown with evidence for every criterion. Publication checks
that evidence refers to the same policy content, base, and reviewed head.
Missing, extra, or stale criterion evidence cannot yield a would-approve result.

Code also requires an open, non-draft PR; a complete review without limitations;
the policy's risk and confidence thresholds; no unresolved findings; no changed
human-review paths; and no outstanding GitHub change requests. Human ownership
and discussion requirements are assessed by the reviewer under the policy;
this feature does not claim to implement GitHub's merge or branch-protection rules.

All available external checks and commit statuses at the reviewed head must
succeed, including any explicitly named `required_checks`. The current Open SWE
review check is excluded by its recorded ID because publication completes that
check. Missing checks, no external check results, pending runs, neutral/skipped
conclusions, and unreadable or incomplete GitHub responses are unknown. Failed
checks are a policy failure. Repository prose cannot override these checks.

The result is a snapshot at publication time. Later CI completion or human
review changes require another review to produce a new evaluation; the old
snapshot remains unchanged. Record creation and recovery never use GitHub's
APPROVE operation.

## Feedback

The GitHub comment shows a compact decision and risk score, with the explanation
collapsed under **Why this decision?** It links to the exact assessment in the dashboard, which displays
the policy source/version, base and head commits, and each criterion's evidence.

React to the GitHub review comment with **👍 useful** or **👎 unhelpful**.
The app collects these reactions through GitHub GraphQL; GitHub does not send
reaction webhooks. A model-free scheduler checks batches of 25 published
assessments every five minutes, cycling through all saved assessments, including
earlier commits. The dashboard shows the latest collected counts and sync time.
Startup maintains the collector schedule and retries registration failures; shared
database locks prevent duplicate registration and overlapping collection runs.
Removing a reaction removes that vote on the next successful collection. Bots,
other emojis, and people with both thumbs selected do not affect the counts.
An incomplete GitHub response preserves the last complete snapshot.

Reactions rate usefulness, not approval eligibility. They never change the
score, policy decision, or a person's explicit corrected decision. No reaction
means no feedback, and this first version does not automatically rewrite policies
or train the reviewer from votes.

For more context, use the comment's **Add context or suggest a different decision**
link. An explanation and a corrected decision are independently optional; provide
at least one, then select **Save feedback**. Links from GitHub open this form automatically;
the detailed evidence remains collapsed under **Why this decision?**

Feedback is authenticated, checked for repository access, and
stored once per person per assessment; a later submission updates that person's
answer. Older links retain their policy and commit context after the PR changes.

Feedback labels are `safe` (would approve), `needs_review` (needs human review),
and `unsure` (insufficient evidence). They record a corrected decision, not a
GitHub approval. The private-task `submit_review_risk_feedback` tool can record
an authenticated user's explicit judgment as well.
