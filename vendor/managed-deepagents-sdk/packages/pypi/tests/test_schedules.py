"""Unit tests for the ``schedules`` authoring API."""

from __future__ import annotations

import pytest

from managed_deepagents import define_schedule
from managed_deepagents.schedules import ScheduleDefinition


class TestDefineSchedule:
    def test_returns_a_tagged_definition(self) -> None:
        schedule = define_schedule(
            cron="0 8 * * 1-5",
            timezone="UTC",
            prompt="Write the daily digest.",
            context={"team": "eng"},
        )

        assert isinstance(schedule, ScheduleDefinition)
        assert schedule.kind == "schedule"
        assert schedule.config["cron"] == "0 8 * * 1-5"
        assert schedule.config["prompt"] == "Write the daily digest."
        assert schedule.config["context"] == {"team": "eng"}
        with pytest.raises(ValueError, match="context"):
            define_schedule(cron="0 8 * * *", prompt="x", context=[])  # type: ignore[arg-type]

    def test_accepts_structured_input(self) -> None:
        schedule = define_schedule(
            cron="30 2 * * *",
            input={"messages": [{"role": "user", "content": "Sweep stale tickets."}]},
        )

        assert schedule.config["input"] == {
            "messages": [{"role": "user", "content": "Sweep stale tickets."}]
        }

    def test_rejects_non_five_field_cron(self) -> None:
        with pytest.raises(ValueError, match="5-field cron"):
            define_schedule(
                cron="0 8 * *",
                prompt="Digest",
            )

    def test_requires_exactly_one_of_prompt_or_input(self) -> None:
        with pytest.raises(ValueError, match="exactly one of `prompt` or `input`"):
            define_schedule(
                cron="0 8 * * *",
            )

        with pytest.raises(ValueError, match="exactly one of `prompt` or `input`"):
            define_schedule(
                cron="0 8 * * *",
                prompt="Digest",
                input={"text": "Digest"},
            )

    def test_requires_persistent_thread_id(self) -> None:
        with pytest.raises(ValueError, match=r"thread.*id"):
            define_schedule(
                cron="0 8 * * *",
                prompt="Digest",
                thread={"mode": "persistent"},
            )

    def test_accepts_slack_deliver_to(self) -> None:
        schedule = define_schedule(
            cron="0 9 * * 1",
            prompt="Say hello",
            deliver_to={
                "channel": "slack",
                "to": {"type": "provider_conversation", "conversation_id": "C123"},
                "auto_post": True,
            },
        )
        assert schedule.config["deliver_to"]["channel"] == "slack"
        assert schedule.config["deliver_to"]["to"] == {
            "type": "provider_conversation",
            "conversation_id": "C123",
        }

    def test_rejects_deliver_to_current_thread(self) -> None:
        with pytest.raises(ValueError, match="current_thread"):
            define_schedule(
                cron="0 9 * * 1",
                prompt="Say hello",
                deliver_to={
                    "channel": "slack",
                    "to": {"type": "current_thread"},  # type: ignore[typeddict-item]
                },
            )
