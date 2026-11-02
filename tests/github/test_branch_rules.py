from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock

import httpx2
from sqlalchemy import update

from openswe.database import postgres
from openswe.github.branch_rules import BRANCH_RULE_FRESH_FOR, BranchMergeRule
from openswe.github.http import GitHubClient


async def test_the_cached_rule_is_reused_until_stale_and_kept_when_github_fails(
    registry_db: None, monkeypatch
) -> None:
    repo = GitHubClient(MagicMock()).repo("acme", "app")
    read = AsyncMock(return_value=True)
    monkeypatch.setattr(type(repo), "requires_conversation_resolution", read)

    assert await BranchMergeRule.requires_conversation_resolution_on(repo, "main")
    assert await BranchMergeRule.requires_conversation_resolution_on(repo, "main")
    assert read.await_count == 1

    async with postgres.session() as session:
        await session.execute(
            update(BranchMergeRule).values(
                updated_at=BranchMergeRule.updated_at - BRANCH_RULE_FRESH_FOR - timedelta(1)
            )
        )
    read.side_effect = httpx2.ConnectError("down")
    assert await BranchMergeRule.requires_conversation_resolution_on(repo, "main")
    assert read.await_count == 2
    assert not await BranchMergeRule.requires_conversation_resolution_on(repo, "release")
