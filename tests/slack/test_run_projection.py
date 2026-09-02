from typing import Any
from unittest.mock import MagicMock

import pytest

from agent import dispatch
from agent.surfaces import projector

CHANNEL_LOCATION = {
    "channel_id": "C-code",
    "thread_ts": "0",
    "surface": "slack_channel",
    "team_id": "T1",
    "triggering_user_id": "U1",
    "triggering_event_ts": "1717171717.000100",
}


@pytest.fixture
def projection(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    started = MagicMock()
    monkeypatch.setattr(projector, "start_projection", started)
    return started


def _dispatch(configurable: dict[str, Any] | None, run: dict[str, Any] | None = None) -> None:
    dispatch._project_run(
        MagicMock(),
        "thread-1",
        run if run is not None else {"run_id": "run-1"},  # type: ignore[arg-type]
        {"configurable": configurable} if configurable is not None else {},
    )


def test_a_channel_run_is_projected_wherever_it_was_dispatched_from(
    projection: MagicMock,
) -> None:
    _dispatch({"slack_thread": CHANNEL_LOCATION})

    kwargs = projection.call_args.kwargs
    assert kwargs["thread_id"] == "thread-1"
    assert kwargs["run_id"] == "run-1"
    assert kwargs["surface"].channel_id == "C-code"
    assert kwargs["location"].team_id == "T1"


def test_a_channel_run_in_a_user_thread_streams_into_that_thread(
    projection: MagicMock,
) -> None:
    _dispatch({"slack_thread": {**CHANNEL_LOCATION, "reply_thread_ts": "1717171718.000200"}})

    assert projection.call_args.kwargs["surface"].reply_target() == "1717171718.000200"


@pytest.mark.parametrize(
    "configurable",
    [
        None,
        {},
        {"slack_thread": None},
        {"slack_thread": {"channel_id": "C1", "thread_ts": "1717171717.000100"}},
    ],
    ids=["no config", "no location", "null location", "slack thread"],
)
def test_surfaces_that_read_the_thread_themselves_are_not_projected(
    projection: MagicMock, configurable: dict[str, Any] | None
) -> None:
    _dispatch(configurable)
    projection.assert_not_called()


def test_a_run_without_an_id_is_not_projected(projection: MagicMock) -> None:
    _dispatch({"slack_thread": CHANNEL_LOCATION}, run={})
    projection.assert_not_called()


def test_a_projection_that_cannot_start_does_not_fail_the_dispatch(
    projection: MagicMock,
) -> None:
    projection.side_effect = RuntimeError("no event loop")

    _dispatch({"slack_thread": CHANNEL_LOCATION})

    projection.assert_called_once()
