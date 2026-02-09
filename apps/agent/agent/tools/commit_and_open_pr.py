import logging
from typing import Any

logger = logging.getLogger(__name__)


def commit_and_open_pr(
    title: str,
    body: str,
    commit_message: str | None = None,
) -> dict[str, Any]:
    """Commit all current changes and open a GitHub Pull Request.

    You MUST call this tool when you have completed your work and want to
    submit your changes for review. This is the final step in your workflow.

    Before calling this tool, ensure you have:
    1. Reviewed your changes for correctness
    2. Run `make format` and `make lint` if a Makefile exists in the repo root

    ## Title Format (REQUIRED — keep under 70 characters)

    The PR title MUST follow this exact format:

        <type>: <short lowercase description> [closes <PROJECT_ID>-<ISSUE_NUMBER>]

    The description MUST be entirely lowercase (no capital letters).

    Where <type> is one of:
    - fix:   for bug fixes
    - feat:  for new features
    - chore: for maintenance tasks (deps, configs, cleanup)
    - ci:    for CI/CD changes

    The [closes ...] suffix links and auto-closes the Linear ticket.
    Use the linear_project_id and linear_issue_number from your context.

    Examples:
    - "fix: resolve null pointer in user auth [closes AA-123]"
    - "feat: add dark mode toggle to settings [closes ENG-456]"
    - "chore: upgrade dependencies to latest versions [closes OPS-789]"

    ## Body Format (REQUIRED)

    The PR body MUST follow this exact template:

        ## Description
        <Explain WHY this PR is needed. Include:
        - List of changes made
        - Reference to the Linear issue or design docs
        - Any context on the approach taken>

        ## Test Plan
        - [ ] <specific test step 1>
        - [ ] <specific test step 2>

    Example body:

        ## Description
        Fixes the null pointer exception that occurs when a user without
        a profile attempts to authenticate. The root cause was a missing
        null check in the `getProfile` method.

        Changes:
        - Added null check in `auth/getProfile.ts`
        - Added fallback default profile object
        - Updated related unit tests

        Resolves AA-123

        ## Test Plan
        - [ ] Verify login works for users without profiles
        - [ ] Verify existing users are unaffected
        - [ ] Run `yarn test` and confirm all tests pass

    ## Commit Message

    The commit message should be concise (1-2 sentences) and focus on the "why"
    rather than the "what". Summarize the nature of the changes: new feature,
    bug fix, refactoring, etc. If not provided, the PR title is used.

    Args:
        title: PR title following the format above (e.g. "fix: resolve auth bug [closes AA-123]")
        body: PR description following the template above with ## Description and ## Test Plan
        commit_message: Optional git commit message. If not provided, the PR title is used.

    Returns:
        Dictionary with the result of the operation including PR URL if successful.
    """
    return {
        "title": title,
        "body": body,
        "commit_message": commit_message or title,
    }
