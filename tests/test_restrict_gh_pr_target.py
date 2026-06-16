"""Unit tests for ``_foreign_pr_target`` in RestrictGhPrTargetMiddleware.

Guards against the regression in traces 019ecd8f / 019ecda6 where the
reviewer ran ``gh pr diff <OTHER_NUMBER>`` after being asked to review
PR #31520 and published "no inline findings" against the wrong diff.
"""

from __future__ import annotations

from agent.middleware.restrict_gh_pr_target import _foreign_pr_target

CONFIGURED = 31520


class TestForeignPrTarget:
    def test_matching_pr_diff_is_allowed(self) -> None:
        cmd = "GH_TOKEN=dummy gh pr diff 31520 --repo owner/name"
        assert _foreign_pr_target(cmd, CONFIGURED) is None

    def test_foreign_pr_diff_is_flagged(self) -> None:
        cmd = "GH_TOKEN=dummy gh pr diff 31517 --repo owner/name"
        assert _foreign_pr_target(cmd, CONFIGURED) == ("diff", 31517)

    def test_foreign_pr_view_is_flagged(self) -> None:
        cmd = "GH_TOKEN=dummy gh pr view 99 --json title"
        assert _foreign_pr_target(cmd, CONFIGURED) == ("view", 99)

    def test_foreign_pr_checkout_is_flagged(self) -> None:
        cmd = "gh pr checkout 42"
        assert _foreign_pr_target(cmd, CONFIGURED) == ("checkout", 42)

    def test_hash_prefixed_number_is_recognized(self) -> None:
        cmd = "GH_TOKEN=dummy gh pr diff #31517 --repo owner/name"
        assert _foreign_pr_target(cmd, CONFIGURED) == ("diff", 31517)

    def test_flag_value_with_digits_is_not_mistaken_for_pr(self) -> None:
        cmd = "GH_TOKEN=dummy gh pr diff --repo owner/name 31520"
        assert _foreign_pr_target(cmd, CONFIGURED) is None

    def test_gh_repo_clone_is_ignored(self) -> None:
        cmd = "GH_TOKEN=dummy gh repo clone owner/name"
        assert _foreign_pr_target(cmd, CONFIGURED) is None

    def test_gh_api_is_ignored(self) -> None:
        cmd = "GH_TOKEN=dummy gh api repos/owner/name/compare/abc...def"
        assert _foreign_pr_target(cmd, CONFIGURED) is None

    def test_gh_pr_create_is_ignored(self) -> None:
        cmd = 'gh pr create --title "x" --body "y"'
        assert _foreign_pr_target(cmd, CONFIGURED) is None

    def test_pure_shell_command_is_ignored(self) -> None:
        cmd = "ls -la"
        assert _foreign_pr_target(cmd, CONFIGURED) is None

    def test_malformed_quoting_does_not_raise(self) -> None:
        cmd = "gh pr diff 'unterminated"
        assert _foreign_pr_target(cmd, CONFIGURED) is None
