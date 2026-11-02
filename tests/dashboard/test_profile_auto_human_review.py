from openswe.dashboard.profiles import ProfileUpdate, get_profile, upsert_profile


async def test_auto_human_review_is_personal_and_survives_other_profile_edits(user_records):
    flag = "auto_human_review_enabled"
    await upsert_profile("alice", "", ProfileUpdate(auto_human_review_enabled=True))
    await upsert_profile("bob", "", ProfileUpdate(auto_human_review_enabled=False))
    await upsert_profile("alice", "", ProfileUpdate(draft_prs=False))
    alice = await get_profile("alice")
    bob = await get_profile("bob")
    assert alice is not None and alice[flag] is True
    assert bob is not None and bob[flag] is False
    await upsert_profile("alice", "", ProfileUpdate(auto_human_review_enabled=False))
    alice = await get_profile("alice")
    assert alice is not None and alice[flag] is False
