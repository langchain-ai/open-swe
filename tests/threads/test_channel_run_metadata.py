"""A channel endpoint answers MDA with only the run metadata keys MDA takes."""

import pytest

from openswe.threads.router import channel_run_metadata
from tests.mda_helpers import mda_starts

_KEYS = (
    # Open SWE's own run metadata.
    "ls_repo_provider",
    "ls_attribution_identifier",
    "user_id",
    "LANGSMITH_AGENT_VERSION",
    "repo",
    "issue.number",
    # MDA's, the Agent Server's and LangSmith's, in any case.
    "mda_delivery_key",
    "langgraph_step",
    "Thread_ID",
    "session_id",
    "conversation_id",
    "run_id",
    "assistant_id",
    "owner",
    "constructor",
    # Keys MDA can't name.
    "a b",
    "_private",
    "1st",
    "k" * 64,
    "k" * 65,
)


@pytest.mark.parametrize("key", _KEYS)
def test_keeps_a_key_exactly_when_mda_takes_it(key: str) -> None:
    kept = key in channel_run_metadata({key: "x"})
    try:
        mda_starts({"input": "go", "metadata": {key: "x"}})
    except TypeError:
        taken = False
    else:
        taken = True
    assert kept is taken


def test_mda_starts_the_run_its_metadata_is_answered_with() -> None:
    metadata = channel_run_metadata(dict.fromkeys(_KEYS, "x"))
    assert mda_starts({"input": "go", "metadata": metadata})["metadata"] == metadata
    assert set(metadata) == {"LANGSMITH_AGENT_VERSION", "repo", "issue.number", "k" * 64}
