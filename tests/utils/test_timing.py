import pytest

from openswe.utils import timing
from openswe.utils.timing import phase


def test_a_phase_entered_again_adds_to_the_time_it_has(monkeypatch: pytest.MonkeyPatch) -> None:
    ticks = iter([0.0, 0.3, 1.0, 1.1])
    monkeypatch.setattr(timing, "perf_counter", lambda: next(ticks))
    timings: dict[str, float] = {}

    with phase(timings, "mda"):
        pass
    with phase(timings, "mda"):
        pass

    assert timings == {"mda": pytest.approx(400.0)}
