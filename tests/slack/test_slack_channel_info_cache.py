import agent.slack.client as slack_client


def test_channel_info_cache_write_prunes_expired_entries(monkeypatch) -> None:
    now = 1_000.0
    monkeypatch.setattr(slack_client.time, "time", lambda: now)
    slack_client.clear_slack_channel_info_cache()
    try:
        slack_client._cache_slack_channel_info("stale", {"name": "old"})
        monkeypatch.setattr(
            slack_client.time,
            "time",
            lambda: now + slack_client.SLACK_CHANNEL_INFO_CACHE_TTL_SECONDS + 1,
        )

        slack_client._cache_slack_channel_info("fresh", {"name": "new"})

        assert "stale" not in slack_client._SLACK_CHANNEL_INFO_CACHE
        assert slack_client._cached_slack_channel_info("fresh") == {"name": "new"}
    finally:
        slack_client.clear_slack_channel_info_cache()


def test_channel_info_cache_is_bounded(monkeypatch) -> None:
    monkeypatch.setattr(slack_client, "SLACK_CHANNEL_INFO_CACHE_MAX_ENTRIES", 2)
    slack_client.clear_slack_channel_info_cache()
    try:
        for channel_id in ("first", "second", "third"):
            slack_client._cache_slack_channel_info(channel_id, {"name": channel_id})

        assert len(slack_client._SLACK_CHANNEL_INFO_CACHE) == 2
        assert "first" not in slack_client._SLACK_CHANNEL_INFO_CACHE
        assert "second" in slack_client._SLACK_CHANNEL_INFO_CACHE
        assert "third" in slack_client._SLACK_CHANNEL_INFO_CACHE
    finally:
        slack_client.clear_slack_channel_info_cache()
