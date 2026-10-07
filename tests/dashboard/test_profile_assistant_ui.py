import pytest

from openswe.dashboard.profiles import ProfileUpdate, get_profile, upsert_profile


@pytest.mark.parametrize("flag", ["experimental_assistant_ui", "experimental_mcp_ptc"])
async def test_conversation_preference_is_personal_and_survives_other_profile_edits(
    fake_store, flag
):
    defaults = {"default_model": "openai:gpt-5.6-sol", "reasoning_effort": "medium"}
    await upsert_profile("alice", "", ProfileUpdate(**defaults, **{flag: True}))
    await upsert_profile("bob", "", ProfileUpdate(**defaults, **{flag: False}))
    await upsert_profile("alice", "", ProfileUpdate(**defaults, draft_prs=False))
    alice = await get_profile("alice")
    bob = await get_profile("bob")
    assert alice is not None and alice[flag] is True
    assert bob is not None and bob[flag] is False
    await upsert_profile("alice", "", ProfileUpdate(**defaults, **{flag: False}))
    alice = await get_profile("alice")
    assert alice is not None and alice[flag] is False
