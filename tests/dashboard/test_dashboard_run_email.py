from collections.abc import Callable

import pytest
from support.langgraph_fakes import FakeLangGraphClient, FakeStore

from agent.settings import user_mappings as um
from agent.utils.run_metadata import resolve_run_email


@pytest.fixture()
def fake_store(patched_langgraph_client: Callable[..., FakeLangGraphClient]) -> FakeStore:
    um.clear_cache()
    return patched_langgraph_client().store


@pytest.mark.asyncio
async def test_run_email_prefers_github_mapping(fake_store: FakeStore) -> None:
    await um.upsert_mapping(
        github_login="johannes117",
        work_email="johannes@langchain.dev",
    )
    # OAuth profile carries a personal account that isn't an org member.
    profile = {"email": "johannesduplessis117@gmail.com"}
    assert await resolve_run_email("johannes117", profile) == "johannes@langchain.dev"


@pytest.mark.asyncio
async def test_run_email_falls_back_to_profile_when_unmapped(fake_store: FakeStore) -> None:
    profile = {"email": "someone@example.com"}
    assert await resolve_run_email("nomapping", profile) == "someone@example.com"
