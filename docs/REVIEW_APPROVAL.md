# Approval policy evaluation

Open SWE evaluates whether a reviewed PR could be approved under a versioned
policy. It publishes **Would approve**, **Needs human review**, or **Insufficient
evidence** alongside the PR risk score. This is a shadow evaluation: GitHub
reviews remain comments, and Open SWE does not grant approval or merge the PR.

The default policy allows risk at most 2/5 with high confidence and requires a
bounded change, relevant verification, and no outstanding need for human
judgment. Every required criterion must pass. A known failure routes to human
review; otherwise, missing evidence produces an insufficient-evidence result.

## Configure policy in Open SWE

Open **Open SWE Review → Approval policy** (`/review/approval`). Approval
requirements are separate from learned review style prompts and feedback.
Open SWE admins can edit the shared policy or override it for an accessible
repository. Other users can read policies for repositories they can access.
The shared default applies across this Open SWE instance.

Use the structured fields for maximum risk (1–5), minimum confidence, required
check names, and paths requiring human review. Write natural-language criteria
in Markdown, with a unique `##` heading for each requirement:

```markdown
## Limited impact
The change has a narrow, well-understood effect. Explain affected behavior and callers.

## Verification
Cite inspected verification of the changed behavior. Missing context means unknown.
```

Repository policies **replace** the shared policy in full: thresholds, required
checks, human-review paths, and natural-language criteria all come from the
repository override. An override may be more or less restrictive. Empty lists
and empty repository criteria stay empty. Without an override, the repository
inherits the shared policy, including future shared edits. New overrides start
with a copy of the shared values in the editor. The editor shows the effective
limits and the shared default for reference. Each scope supports up to 100 check
names and 100 path patterns, and 1–20 criteria within 24 KB (repository criteria
can be empty).

**Save policy** creates a revision recording the author and timestamp. **Reset**
returns a repository to the latest shared policy, or returns shared settings to
the built-in default. Shared edits do not change an existing repository override.
Concurrent edits to the effective policy are rejected; reload before reconciling
them.
The private-admin `manage_review_approval_policy` tool exposes the same read,
save, and reset operations, with repository access and version checks.

`APPROVAL_POLICY.md` files are not loaded. A PR cannot change its approval
requirements by editing repository files. Existing assessments made with older
repository-file policies retain their original policy snapshot.

Path patterns use case-sensitive shell wildcard matching over the full
repository-relative path; `*` matches slashes. Renames inspect both old and new
paths. Missing customization uses the built-in policy. Unreadable or invalid
settings produce unknown eligibility; they never silently fall back to defaults.

## Evidence and decisions

The reviewer calls `get_review_approval_policy`, inspects the entire PR, and
reports pass/fail/unknown with evidence for every criterion. Publication checks
that evidence refers to the same effective settings version, base, and reviewed head.
Settings are rechecked after collecting GitHub evidence; changes during a review
require the reviewer to read and assess the current policy again.
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
checks are a policy failure. Natural-language criteria cannot override these checks.

The result is a snapshot at publication time. Later CI completion or human
review changes require another review to produce a new evaluation; the old
snapshot remains unchanged. Record creation and recovery never use GitHub's
APPROVE operation.

## Feedback

The GitHub comment shows a compact decision and risk score, with the explanation
collapsed under **Why this decision?** It links to the exact assessment in the dashboard, which displays
the policy source/version, base and head commits, and each criterion's evidence.
Policy edits never rewrite a published assessment.

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
