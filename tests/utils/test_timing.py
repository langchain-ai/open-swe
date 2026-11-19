import pytest

from openswe.utils import timing
from openswe.utils.startup_trace import apm_span
from openswe.utils.timing import RequestTimings, phase


def test_a_phase_entered_again_adds_to_the_time_it_has(monkeypatch: pytest.MonkeyPatch) -> None:
    ticks = iter([0.0, 0.3, 1.0, 1.1])
    monkeypatch.setattr(timing, "perf_counter", lambda: next(ticks))
    timings: dict[str, float] = {}

    with phase(timings, "mda"):
        pass
    with phase(timings, "mda"):
        pass

    assert timings == {"mda": pytest.approx(400.0)}


def test_a_logged_request_reports_each_step_it_ran(caplog: pytest.LogCaptureFixture) -> None:
    details: dict[str, object] = {"pull_request_url": "https://github.com/lc/repo/pull/7"}

    with caplog.at_level("INFO", logger="openswe.utils.timing"):
        with RequestTimings.logged("Review request finished", details):
            with apm_span("human_review.blockers", {}):
                pass
            with apm_span("human_review.save_request", {}):
                pass
            with apm_span("human_review.save_request", {}):
                pass
            details["success"] = True
    # Outside a logged request, a step is only traced.
    with apm_span("human_review.blockers", {}):
        pass

    (record,) = caplog.records
    assert record.getMessage() == "Review request finished"
    assert vars(record)["pull_request_url"] == "https://github.com/lc/repo/pull/7"
    assert vars(record)["success"] is True
    assert vars(record)["outcome"] == "ok"
    assert set(vars(record)["steps_ms"]) == {"human_review_blockers", "human_review_save_request"}
    assert vars(record)["slowest_step"] in {"human_review.blockers", "human_review.save_request"}
    assert isinstance(vars(record)["elapsed_ms"], int)


def test_a_request_that_fails_is_still_logged(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level("INFO", logger="openswe.utils.timing"):
        with pytest.raises(RuntimeError), RequestTimings.logged("Review request finished", {}):
            with apm_span("human_review.repo_access", {}):
                raise RuntimeError("GitHub refused")

    (record,) = caplog.records
    assert vars(record)["outcome"] == "error"
    assert vars(record)["slowest_step"] == "human_review.repo_access"
