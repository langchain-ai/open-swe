"""Tests for the process-wide batch processor and retry policy."""

from __future__ import annotations

import math
import threading
import time
from dataclasses import replace
from unittest.mock import Mock, call

import pytest

from managed_deepagents._concurrency import BatchProcessor, Deadline
from managed_deepagents._http_status import HttpStatusError, is_retryable, requested_delay
from managed_deepagents._resilience import RetryPolicy, backoff_seconds, with_retry


class _Recorder:
    def __init__(self, *, fail_first: bool = False) -> None:
        self.batches: list[list[int]] = []
        self.calls = 0
        self.in_flight = 0
        self.max_in_flight = 0
        self._fail_first = fail_first
        self._lock = threading.Lock()

    def __call__(self, batch: list[int], _deadline: Deadline) -> None:
        with self._lock:
            self.calls += 1
            self.in_flight += 1
            self.max_in_flight = max(self.max_in_flight, self.in_flight)
            call = self.calls
        time.sleep(0.001)
        with self._lock:
            self.in_flight -= 1
        if self._fail_first and call == 1:
            raise RuntimeError("boom")
        self.batches.append(batch)


def _processor(recorder: _Recorder, **overrides: float) -> BatchProcessor[int]:
    options = {"max_batch_size": 2, "max_wait_seconds": 10.0, "max_queued": 10, **overrides}
    return BatchProcessor(
        handle=recorder,
        max_batch_size=int(options["max_batch_size"]),
        max_wait_seconds=options["max_wait_seconds"],
        max_queued=int(options["max_queued"]),
    )


def test_handles_a_full_batch_without_waiting_out_the_window() -> None:
    recorder = _Recorder()
    processor = _processor(recorder)

    processor.push(1, 2)
    deadline = time.monotonic() + 2
    while not recorder.batches and time.monotonic() < deadline:
        time.sleep(0.01)

    assert recorder.batches == [[1, 2]]


def test_handles_a_partial_batch_after_the_window() -> None:
    recorder = _Recorder()
    processor = _processor(recorder, max_wait_seconds=0.05)

    processor.push(1)
    time.sleep(0.02)
    assert recorder.batches == []
    time.sleep(0.2)

    assert recorder.batches == [[1]]


def test_idle_waits_until_everything_is_handled() -> None:
    recorder = _Recorder()
    processor = _processor(recorder, max_wait_seconds=0.05)

    processor.push(1, 2, 3)

    assert processor.idle(timeout=2)
    assert recorder.batches == [[1, 2], [3]]


def test_ends_a_batch_before_it_passes_the_byte_limit_sending_a_big_item_alone() -> None:
    recorder = _Recorder()
    processor = BatchProcessor[int](
        handle=recorder,
        max_batch_size=10,
        max_wait_seconds=10,
        max_queued=10,
        max_batch_bytes=10,
        size_of=lambda item: item,
    )

    processor.push(4, 5, 3, 20, 1)
    processor.close(timeout=2)

    assert recorder.batches == [[4, 5], [3], [20], [1]]


def test_drops_items_past_the_queue_bound() -> None:
    processor = BatchProcessor[int](
        handle=lambda _batch, _deadline: time.sleep(1),
        max_batch_size=100,
        max_wait_seconds=10,
        max_queued=3,
    )

    assert processor.push(1, 2) == 2
    assert processor.push(3, 4) == 1
    assert processor.push(5) == 0


def test_handles_one_batch_at_a_time_and_survives_a_failure() -> None:
    recorder = _Recorder(fail_first=True)
    processor = _processor(recorder, max_batch_size=1)

    processor.push(1, 2, 3)

    assert processor.idle(timeout=2)
    assert recorder.batches == [[2], [3]]
    assert recorder.max_in_flight == 1


def test_close_drains_the_queue_without_the_batch_wait_then_refuses_more() -> None:
    recorder = _Recorder()
    processor = _processor(recorder)

    processor.push(1, 2, 3, 4, 5)
    started = time.monotonic()
    processor.close(timeout=2)

    assert time.monotonic() - started < 1
    assert recorder.batches == [[1, 2], [3, 4], [5]]
    assert recorder.max_in_flight == 1
    assert processor.push(6) == 0


def test_close_caps_a_backoff_during_the_drain_at_the_time_left() -> None:
    attempts: list[int] = []

    def unavailable(_batch: list[int], _deadline: Deadline) -> None:
        attempts.append(1)
        raise _RetryableError(after=30.0)

    policy = RetryPolicy(
        max_attempts=5,
        base_delay_seconds=30.0,
        max_delay_seconds=30.0,
        retry_on=lambda _error: True,
        delay_for=lambda _error: 30.0,
    )
    processor = BatchProcessor[int](
        handle=with_retry(unavailable, policy),
        max_batch_size=10,
        max_wait_seconds=10,
        max_queued=10,
    )

    processor.push(1)
    started = time.monotonic()
    processor.close(timeout=0.2)

    assert time.monotonic() - started < 1
    assert len(attempts) == 1


def test_close_gives_up_on_a_request_hanging_past_the_deadline() -> None:
    budgets: list[float] = []
    release = threading.Event()

    def hang(_batch: list[int], deadline: Deadline) -> None:
        budgets.append(deadline.remaining())
        release.wait(5)

    processor = BatchProcessor[int](
        handle=hang, max_batch_size=1, max_wait_seconds=0, max_queued=10
    )

    processor.push(1)
    while not budgets:
        time.sleep(0.01)
    processor.push(2)
    started = time.monotonic()
    processor.close(timeout=0.2)
    release.set()

    assert time.monotonic() - started < 1
    assert budgets == [math.inf]
    time.sleep(0.05)
    assert len(budgets) == 1


class _RetryableError(Exception):
    def __init__(self, after: float | None = None) -> None:
        super().__init__("retryable")
        self.after = after


POLICY = RetryPolicy(
    max_attempts=3,
    base_delay_seconds=0.1,
    max_delay_seconds=1.0,
    retry_on=lambda error: isinstance(error, _RetryableError),
    delay_for=lambda error: error.after if isinstance(error, _RetryableError) else None,
)


class _RecordingDeadline(Deadline):
    """Records waits instead of sleeping; ``passes_after`` waits end it."""

    def __init__(self, passes_after: int | None = None) -> None:
        super().__init__()
        self.waits: list[float] = []
        self._passes_after = passes_after

    def wait(self, seconds: float) -> None:
        self.waits.append(seconds)
        if self._passes_after is not None and len(self.waits) >= self._passes_after:
            self.set(0)


def _failing(times: int, after: float | None = None) -> tuple[list[int], object]:
    attempts: list[int] = []

    def fn(_arg: None, _deadline: Deadline) -> str:
        attempts.append(1)
        if len(attempts) <= times:
            raise _RetryableError(after=after)
        return "ok"

    return attempts, fn


def test_retries_until_the_call_succeeds_waiting_what_the_error_asks() -> None:
    deadline = _RecordingDeadline()
    _, fn = _failing(1, after=0.9)

    assert with_retry(fn, POLICY)(None, deadline) == "ok"
    assert deadline.waits == [0.9]


def test_retry_preserves_the_argument_and_deadline() -> None:
    deadline = _RecordingDeadline()
    arg = {"batch": [1, 2]}
    fn = Mock(side_effect=[_RetryableError(after=0), "ok"])

    assert with_retry(fn, POLICY)(arg, deadline) == "ok"
    assert fn.call_count == 2
    for args in fn.call_args_list:
        assert args.args[0] is arg
        assert args.args[1] is deadline


@pytest.mark.parametrize("with_delay_hint", [False, True])
def test_retry_uses_exponential_backoff_without_a_requested_delay(
    monkeypatch: pytest.MonkeyPatch, *, with_delay_hint: bool
) -> None:
    monkeypatch.setattr("managed_deepagents._resilience.retry.random.random", lambda: 0.5)
    deadline = _RecordingDeadline()
    fn = Mock(side_effect=[_RetryableError(), _RetryableError(), "ok"])
    policy = POLICY if with_delay_hint else replace(POLICY, delay_for=None)

    assert with_retry(fn, policy)(None, deadline) == "ok"
    assert deadline.waits == pytest.approx([0.075, 0.15])
    assert fn.call_args_list == [call(None, deadline)] * 3


def test_retry_honors_a_zero_requested_delay() -> None:
    deadline = _RecordingDeadline()
    fn = Mock(side_effect=[_RetryableError(after=0), "ok"])

    assert with_retry(fn, POLICY)(None, deadline) == "ok"
    assert deadline.waits == [0]
    assert fn.call_count == 2


def test_retry_preserves_the_exact_final_error() -> None:
    deadline = _RecordingDeadline()
    final_error = _RetryableError(after=0)
    fn = Mock(side_effect=[_RetryableError(after=0), _RetryableError(after=0), final_error])

    with pytest.raises(_RetryableError) as caught:
        with_retry(fn, POLICY)(None, deadline)

    assert caught.value is final_error
    assert fn.call_count == 3
    assert deadline.waits == [0, 0]


def test_retry_does_not_wait_when_the_failed_call_expires_the_deadline() -> None:
    deadline = _RecordingDeadline()
    error = _RetryableError()

    def fail(_arg: None, received_deadline: Deadline) -> None:
        received_deadline.set(0)
        raise error

    fn = Mock(side_effect=fail)
    with pytest.raises(_RetryableError) as caught:
        with_retry(fn, POLICY)(None, deadline)

    assert caught.value is error
    assert fn.call_count == 1
    assert deadline.waits == []


def test_caps_an_asked_for_delay_at_the_policy_max() -> None:
    deadline = _RecordingDeadline()
    _, fn = _failing(1, after=60.0)

    assert with_retry(fn, POLICY)(None, deadline) == "ok"
    assert deadline.waits == [1.0]


def test_does_not_retry_an_error_the_policy_rejects() -> None:
    attempts: list[int] = []

    def fn(_arg: None, _deadline: Deadline) -> None:
        attempts.append(1)
        raise ValueError("fatal")

    with pytest.raises(ValueError, match="fatal"):
        with_retry(fn, POLICY)(None, _RecordingDeadline())
    assert len(attempts) == 1


def test_gives_up_after_the_last_attempt() -> None:
    attempts, fn = _failing(10)

    with pytest.raises(_RetryableError):
        with_retry(fn, POLICY)(None, _RecordingDeadline())
    assert len(attempts) == 3


def test_stops_retrying_once_the_deadline_passes() -> None:
    attempts, fn = _failing(10, after=0.5)

    with pytest.raises(_RetryableError):
        with_retry(fn, POLICY)(None, _RecordingDeadline(passes_after=1))
    assert len(attempts) == 1


def test_backoff_doubles_within_jitter_and_stops_at_the_cap() -> None:
    for attempt, ceiling in ((1, 0.1), (2, 0.2), (3, 0.4), (10, 1.0)):
        delay = backoff_seconds(attempt, POLICY)
        assert ceiling / 2 <= delay <= ceiling


def test_http_status_error_classification() -> None:
    assert HttpStatusError(429).retryable
    assert HttpStatusError(503).retryable
    assert not HttpStatusError(404).retryable
    assert is_retryable(TimeoutError())
    assert not is_retryable(HttpStatusError(401))
    assert HttpStatusError.from_response(429, "3").retry_after_seconds == 3.0
    assert HttpStatusError.from_response(429, "soon").retry_after_seconds is None
    assert requested_delay(HttpStatusError.from_response(429, "2")) == 2.0
    assert requested_delay(TimeoutError()) is None
