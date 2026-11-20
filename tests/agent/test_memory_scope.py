import pytest
from langchain_core.runnables import RunnableConfig

from openswe.agent_api.memory_scope import RunMemoryScope


def _config(**configurable: object) -> RunnableConfig:
    return {"configurable": {"thread_id": "thread-1", **configurable}}


def _slack(**channel: object) -> dict[str, object]:
    internal = {"is_im": False, "is_mpim": False, "is_private": False}
    shared = {"is_ext_shared": False, "is_pending_ext_shared": False}
    context = {"id": "C1", **internal, **shared, **channel}
    return {"slack_thread": {"channel_id": "C1", "thread_ts": "1.0", "channel_context": context}}


def _thread(config: RunnableConfig) -> str:
    return RunMemoryScope.for_run(config).thread


@pytest.mark.parametrize(
    ("channel", "thread"),
    [
        ({}, "public"),
        ({"is_private": True}, "other"),
        ({"is_private": None}, "other"),
        ({"is_mpim": True}, "other"),
        ({"is_ext_shared": True}, "external"),
        ({"is_pending_ext_shared": None}, "external"),
    ],
)
def test_only_a_public_internal_slack_channel_is_public(
    channel: dict[str, object], thread: str
) -> None:
    assert _thread(_config(**_slack(**channel))) == thread


def test_a_dm_is_never_external_though_slack_omits_the_sharing_flags() -> None:
    dm = {"channel_id": "D1", "thread_ts": "1.0", "channel_context": {"id": "D1", "is_im": True}}

    assert _thread(_config(slack_thread=dm)) == "other"


def test_a_slack_thread_slack_said_nothing_about_is_external() -> None:
    assert _thread(_config(slack_thread={"channel_id": "C1", "thread_ts": "1.0"})) == "external"


@pytest.mark.parametrize(("repo_private", "thread"), [(True, "other"), (None, "external")])
def test_a_github_thread_is_external_unless_its_repository_is_private(
    repo_private: bool | None, thread: str
) -> None:
    assert _thread(_config(source="github", repo_private=repo_private)) == thread
