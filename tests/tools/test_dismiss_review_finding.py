from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from openswe.tools.dismiss_review_finding import dismiss_review_finding


@pytest.mark.asyncio
async def test_refuses_a_person_without_access_to_the_repository() -> None:
    config = {"configurable": {"repo": {"owner": "acme", "name": "app"}, "pr_number": 7}}
    dismiss = AsyncMock()
    with (
        patch("openswe.run_config.get_config", return_value=config),
        patch(
            "openswe.tools.review_finding_target.pr_author_login",
            AsyncMock(return_value="mallory"),
        ),
        patch(
            "openswe.tools.review_finding_target.require_repo_access_for_user",
            AsyncMock(side_effect=HTTPException(403, "no access")),
        ),
        patch("openswe.tools.dismiss_review_finding.FindingDismisser.dismiss", dismiss),
    ):
        result = await dismiss_review_finding("f_a", reason="nope")

    assert result == {"success": False, "error": "Repository access denied: no access"}
    dismiss.assert_not_called()
