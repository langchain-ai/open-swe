from agent.dashboard.profiles import ProfileUpdate, get_profile, upsert_profile


async def test_conversation_preference_is_personal_and_survives_other_profile_edits(fake_store):
    defaults = {"default_model": "openai:gpt-5.6-sol", "reasoning_effort": "medium"}
    await upsert_profile("alice", "", ProfileUpdate(**defaults, experimental_assistant_ui=True))
    await upsert_profile("bob", "", ProfileUpdate(**defaults, experimental_assistant_ui=False))
    await upsert_profile("alice", "", ProfileUpdate(**defaults, draft_prs=False))
    alice = await get_profile("alice")
    bob = await get_profile("bob")
    assert alice is not None and alice["experimental_assistant_ui"] is True
    assert bob is not None and bob["experimental_assistant_ui"] is False
    await upsert_profile("alice", "", ProfileUpdate(**defaults, experimental_assistant_ui=False))
    alice = await get_profile("alice")
    assert alice is not None and alice["experimental_assistant_ui"] is False
