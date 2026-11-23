"""A long garbage collection is logged, and startup's objects stay out of later ones."""

import gc
import logging
import time
from collections.abc import Iterator

import pytest

from openswe.utils.event_loop import GcPauses


@pytest.fixture
def _unfrozen() -> Iterator[None]:
    yield
    gc.unfreeze()


def _slow_collection(_phase: str, _info: dict[str, int]) -> None:
    """Stands in for a collection walking a large heap, timed from inside its callbacks."""
    if _phase == "start":
        time.sleep(0.15)


def test_a_long_collection_is_logged_and_a_short_one_is_not(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("GC_PAUSE_LOG_MS", "100")
    caplog.set_level(logging.WARNING, logger="openswe.utils.event_loop")

    with GcPauses.watch():
        gc.collect(0)
        gc.callbacks.append(_slow_collection)
        try:
            gc.collect(2)
        finally:
            gc.callbacks.remove(_slow_collection)

    (paused,) = [r for r in caplog.records if r.message == "Garbage collection paused the process"]
    assert paused.gc_pause_ms >= 100
    assert paused.gc_generation == 2
    assert not any(isinstance(c, GcPauses) for c in gc.callbacks)


def test_logging_is_off_at_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GC_PAUSE_LOG_MS", "0")
    before = list(gc.callbacks)

    with GcPauses.watch():
        assert gc.callbacks == before


@pytest.mark.usefixtures("_unfrozen")
def test_startup_objects_are_frozen_out_of_later_collections() -> None:
    gc.unfreeze()
    before = gc.get_freeze_count()
    # Containers, which the collector tracks: what a full collection would walk.
    kept = [[index] for index in range(1000)]
    assert all(gc.is_tracked(item) for item in kept)

    GcPauses.freeze_startup_objects()

    assert gc.get_freeze_count() - before >= len(kept)


@pytest.fixture
def _thresholds() -> Iterator[None]:
    before = gc.get_threshold()
    yield
    gc.set_threshold(*before)


@pytest.mark.usefixtures("_thresholds")
def test_full_collections_run_a_tenth_as_often(monkeypatch: pytest.MonkeyPatch) -> None:
    """Each walks the whole heap to free a few thousand objects, every ~25s per pod."""
    monkeypatch.delenv("GC_FULL_COLLECTION_EVERY", raising=False)
    young, middle, _ = gc.get_threshold()

    GcPauses.space_out_full_collections()

    assert gc.get_threshold() == (young, middle, 100)


@pytest.mark.usefixtures("_thresholds")
def test_python_keeps_its_own_spacing_at_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GC_FULL_COLLECTION_EVERY", "0")
    before = gc.get_threshold()

    GcPauses.space_out_full_collections()

    assert gc.get_threshold() == before
