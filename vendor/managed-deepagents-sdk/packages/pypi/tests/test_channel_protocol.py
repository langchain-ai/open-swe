"""Unit tests for the managed channel collector."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from managed_deepagents import channels
from managed_deepagents._channel import (
    CHANNEL_BRAND,
    collect_channels,
    is_channel,
    requirements_from_channels,
)


def test_collect_channels_stamps_file_stem_names() -> None:
    channel = channels.slack(name="Support Bot")
    collected = collect_channels([("channels/slack.py", SimpleNamespace(channel=channel))])
    assert len(collected) == 1
    assert collected[0].name == "slack"
    assert is_channel(collected[0])
    assert getattr(collected[0], CHANNEL_BRAND) is True
    assert callable(getattr(collected[0], "events", None))


@pytest.mark.parametrize(
    "source", ["channels/slack.tsx", "channels/slack.cts", "channels/slack.mts"]
)
def test_collect_channels_strips_cli_accepted_typescript_extensions(source: str) -> None:
    channel = channels.slack(name="Support Bot")
    collected = collect_channels([(source, SimpleNamespace(channel=channel))])
    assert collected[0].name == "slack"


def test_collect_channels_skips_unbranded_exports() -> None:
    collected = collect_channels(
        [
            ("channels/helper.py", SimpleNamespace(helper=True)),
            ("channels/fake.py", SimpleNamespace(channel=SimpleNamespace(kind="fake"))),
        ]
    )
    assert collected == []


def test_requirements_from_channels() -> None:
    channel = channels.slack(name="Support Bot")
    collected = collect_channels([("channels/slack.py", SimpleNamespace(channel=channel))])
    requirements = requirements_from_channels(collected)
    assert requirements["slack"]["provider"] == "slack"
    assert requirements["slack"]["requiredEnv"] == []


def test_collect_channels_rejects_duplicate_names() -> None:
    a = channels.slack(name="Support Bot")
    b = channels.slack(name="Support Bot")
    with pytest.raises(ValueError, match="duplicate channel name 'slack'"):
        collect_channels(
            [
                ("channels/slack.py", SimpleNamespace(channel=a)),
                ("channels/slack.py", SimpleNamespace(channel=b)),
            ]
        )
