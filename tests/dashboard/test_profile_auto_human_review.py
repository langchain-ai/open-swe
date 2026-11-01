from openswe.dashboard.profiles import PROFILES, ProfileUpdate, get_profile, upsert_profile


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


async def test_repository_opt_out_preserves_other_reviews_and_survives_profile_edits(user_records):
    await upsert_profile(
        "alice", "", ProfileUpdate(auto_human_review_excluded_repos=[" Org/Repo ", "org/repo"])
    )
    await upsert_profile("alice", "", ProfileUpdate(draft_prs=False))
    assert not await PROFILES.allows_auto_human_review("alice", "ORG/REPO")
    assert await PROFILES.allows_auto_human_review("alice", "org/other")
    assert await PROFILES.allows_auto_human_review("bob", "org/repo")
    await upsert_profile("alice", "", ProfileUpdate(auto_human_review_excluded_repos=[]))
    assert await PROFILES.allows_auto_human_review("alice", "org/repo")
    await upsert_profile("alice", "", ProfileUpdate(auto_human_review_enabled=False))
    assert not await PROFILES.allows_auto_human_review("alice", "org/other")
