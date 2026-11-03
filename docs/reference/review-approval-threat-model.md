# Threat model: stale Open SWE approvals

This document covers the lifetime of Open SWE's GitHub approvals, not the accuracy of its reviews or a complete deployment threat model.
See the [repository trust model](../INSTALLATION.md#repository-trust-model) for deployment boundaries.

## Threat and trust boundary

A GitHub approval can count toward required reviews under branch protection.
If an Open SWE approval remains standing after new code is pushed or a later Open SWE review no longer approves the pull request, GitHub may still accept that stale approval, depending on repository rules.
A contributor able to update the pull request could therefore have unapproved changes satisfy a required-review condition using an earlier approval.

The review assessment and GitHub's stored approval are separate state.
Publishing an advisory comment, disabling automatic review, or turning approval mode off does not itself revoke an existing GitHub approval.

## Mitigations and scope

Open SWE attempts to dismiss superseded approvals at two points:

- **Push:** An accepted `pull_request.synchronize` webhook schedules withdrawal of recognized Open SWE approvals whose commit differs from the event's head SHA.
  Approvals for that SHA are retained.
  This runs independently of whether automatic review is enabled.
- **Later review publication:** After posting a review, Open SWE withdraws older approvals from the same bot login as the posted review, retaining the new review only if it actually submitted an approval.
  A non-approving review therefore attempts to leave no approvals standing from that login.
  This scopes publication cleanup to the publishing deployment's bot identity rather than withdrawing another deployment's approvals.
  If the posted review cannot be found or has no author, scoped cleanup dismisses nothing.
- **Empty re-review:** When publication skips a duplicate empty summary, cleanup instead retains approvals for the reviewed head SHA and attempts to dismiss recognized Open SWE approvals for other commits.

Withdrawal only targets `APPROVED` reviews authored by logins in `OPEN_SWE_GITHUB_LOGINS`; human approvals and other review states are untouched.
Push and empty-re-review cleanup use that recognized-login set across deployments, whereas posted-review cleanup narrows it to the publishing login.
Deployments sharing a bot login are not distinguishable by that scope; custom bot logins outside the set are not covered.

## Residual risk and operator responsibilities

- Cleanup is best-effort, not an atomic merge gate.
  It depends on webhook delivery and acceptance, background execution, review publication reaching cleanup, and GitHub API availability.
  There is a window in which a superseded approval can still count; a missed or failed cleanup can leave it standing.
- The acting GitHub App/token needs permission to dismiss reviews, and repository dismissal restrictions can prevent it.
  Listing or dismissal failures are logged without blocking the remaining webhook/publication work.
  A successful review publication does not prove that prior approvals were withdrawn.
- Disabling approval mode or automatic review does not immediately revoke existing approvals.
  A later push or publication may trigger cleanup, subject to the scope and failure modes above.
  If immediate revocation is required, an authorized maintainer must inspect and dismiss standing approvals in GitHub.
- Configure GitHub branch protection/rulesets, including stale-approval dismissal where appropriate, and required human review independently.
  Do not treat Open SWE's cleanup as a guarantee that only approvals of the current code or latest assessment can satisfy merge requirements.

## Implementation references

- [Webhook scheduling](../../openswe/github/routes.py) and [push cleanup](../../openswe/github/webhook.py): `github_webhook`, `withdraw_superseded_approvals`.
- [Approval selection and dismissal](../../openswe/github/pull_request_status.py): `PullRequestClient.withdraw_open_swe_approvals`.
- [Recognized bot identities](../../openswe/github/org_membership.py): `OPEN_SWE_GITHUB_LOGINS`.
- [Publication cleanup](../../openswe/tools/publish_review.py): `_publish_review_async`, `_withdraw_open_swe_approvals`.
- [Approval-mode changes](../../openswe/tools/manage_review_approval_mode.py): `manage_review_approval_mode`.
