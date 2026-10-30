"""Pinned outputs for every deterministic thread-id derivation.

These ids are persisted and re-derived across processes, so a formula change
must be a deliberate act that updates these literals — not a silent refactor.
"""

from openswe.thread_ids import thread_id_from_branch


def test_thread_id_from_branch() -> None:
    assert (
        thread_id_from_branch("open-swe/0e0feb04-05d3-5925-969e-1051f3741622")
        == "0e0feb04-05d3-5925-969e-1051f3741622"
    )
    assert (
        thread_id_from_branch("open-swe/0E0FEB04-05D3-5925-969E-1051F3741622")
        == "0E0FEB04-05D3-5925-969E-1051F3741622"
    )
    assert thread_id_from_branch("feature/no-uuid-here") is None
