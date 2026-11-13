Re-evaluate a pull request's current-head approval policy and publish Robin Approves, even if the target branch does not require that check.
Available to every linked GitHub user with access to the repository; no admin privileges are needed.
Use when the user explicitly asks to recheck or publish Robin Approves.
This does not run a code review, grant an approval, change repository rules, merge, or mark a draft ready.
The existing policy still applies: Robin-authored PRs must have qualifying approval; other authors pass this check and remain subject to the repository's own review rules.
Report `passed` and `summary`: a successful tool call can publish a failing check.
Closed PRs cannot be rechecked.
When the target branch does not require this check, its result is a snapshot and another explicit recheck is needed to refresh it.
