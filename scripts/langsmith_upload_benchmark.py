"""Benchmark LangSmith trace serialization and multipart upload memory behavior."""

from __future__ import annotations

import argparse
import gc
import hashlib
import json
import resource
import threading
import time
import tracemalloc
import uuid
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from typing import Any, ClassVar

import requests
import zstandard
from langsmith._internal._operations import (
    SerializedRunOperation,
    encode_multipart_parts_and_context,
    serialize_run_dict,
    serialized_run_operation_to_multipart_parts_and_context,
)
from pydantic import BaseModel
from requests_toolbelt.multipart import MultipartEncoder

MIB = 1024 * 1024
BOUNDARY = "open-swe-langsmith-benchmark"


class TraceState(BaseModel):
    dump_calls: ClassVar[int] = 0

    messages: list[dict[str, str]]
    files: dict[str, str]
    metadata: dict[str, int | str]

    def model_dump(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        type(self).dump_calls += 1
        return super().model_dump(*args, **kwargs)


@dataclass(frozen=True)
class Measurement:
    stage: str
    workload: str
    size_bytes: int
    workers: int
    iterations: int
    compression: bool
    elapsed_seconds: float
    throughput_mib_s: float
    tracemalloc_peak_bytes: int
    rss_before_bytes: int
    rss_peak_bytes: int
    rss_settled_bytes: int
    input_bytes: int
    output_bytes: int
    byte_amplification: float
    byte_budget_bytes: int | None
    max_in_flight_bytes: int
    allocation_hotspots: tuple[str, ...]
    model_dump_calls: int
    requests: int = 0
    connections: int = 0


class WeightedSemaphore:
    def __init__(self, capacity: int) -> None:
        self._capacity = capacity
        self._available = capacity
        self.peak_used = 0
        self._condition = threading.Condition()

    def acquire(self, weight: int) -> None:
        weight = min(weight, self._capacity)
        with self._condition:
            self._condition.wait_for(lambda: self._available >= weight)
            self._available -= weight
            self.peak_used = max(self.peak_used, self._capacity - self._available)

    def release(self, weight: int) -> None:
        weight = min(weight, self._capacity)
        with self._condition:
            self._available += weight
            self._condition.notify_all()


class SinkState:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.requests = 0
        self.connections: set[int] = set()
        self.bytes_received = 0


class SinkServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int]) -> None:
        self.state = SinkState()
        super().__init__(address, SinkHandler)


class SinkHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_POST(self) -> None:
        received = 0
        if self.headers.get("Transfer-Encoding") == "chunked":
            while True:
                chunk_size = int(self.rfile.readline().strip(), 16)
                if chunk_size == 0:
                    self.rfile.readline()
                    break
                received += len(self.rfile.read(chunk_size))
                self.rfile.read(2)
        else:
            remaining = int(self.headers.get("Content-Length", "0"))
            while remaining:
                chunk = self.rfile.read(min(remaining, 64 * 1024))
                if not chunk:
                    break
                received += len(chunk)
                remaining -= len(chunk)
        state = self.server.state  # type: ignore[attr-defined]
        with state.lock:
            state.requests += 1
            state.connections.add(id(self.connection))
            state.bytes_received += received
        self.send_response(204)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, format: str, *args: Any) -> None:
        return


def current_rss_bytes() -> int:
    with open("/proc/self/statm", encoding="utf-8") as statm:
        resident_pages = int(statm.read().split()[1])
    return resident_pages * resource.getpagesize()


def peak_rss_bytes() -> int:
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return peak * 1024


def deterministic_text(size_bytes: int, seed: str) -> str:
    return hashlib.shake_256(seed.encode()).hexdigest((size_bytes + 1) // 2)[:size_bytes]


def make_run(size_bytes: int, workload: str, sequence: int = 0) -> dict[str, Any]:
    half = max(1, size_bytes // 2)
    state = {
        "messages": [{"role": "user", "content": deterministic_text(half, f"input-{sequence}")}],
        "files": {"result.txt": deterministic_text(size_bytes - half, f"output-{sequence}")},
        "metadata": {"sequence": sequence, "workload": "synthetic"},
    }
    trace_state: dict[str, Any] | TraceState = state
    if workload == "pydantic":
        trace_state = TraceState.model_validate(state)
    run_id = uuid.uuid5(uuid.NAMESPACE_URL, f"open-swe-benchmark-{sequence}")
    return {
        "id": run_id,
        "trace_id": run_id,
        "dotted_order": f"20260101T000000000000Z{run_id}",
        "name": "synthetic-open-swe-run",
        "run_type": "chain",
        "inputs": {"state": trace_state},
        "outputs": {"state": trace_state},
        "events": [{"name": "benchmark", "time": "2026-01-01T00:00:00Z"}],
        "extra": {"metadata": {"benchmark": True, "sequence": sequence}},
        "attachments": {
            "manifest": (
                "application/json",
                deterministic_text(4096, f"attachment-{sequence}").encode(),
            )
        },
    }


def multipart_parts(operation: SerializedRunOperation) -> list[tuple[str, tuple[Any, ...]]]:
    parts, opened = serialized_run_operation_to_multipart_parts_and_context(operation)
    if opened:
        raise ValueError("Synthetic benchmark unexpectedly opened attachments")
    return parts.parts


def drain_encoder(parts: list[tuple[str, tuple[Any, ...]]]) -> bytes:
    encoder = MultipartEncoder(parts, boundary=BOUNDARY)
    chunks: list[bytes] = []
    while chunk := encoder.read(1024 * 1024):
        chunks.append(chunk)
    return b"".join(chunks)


def stream_multipart(operation: SerializedRunOperation) -> Iterable[bytes]:
    parts, opened = serialized_run_operation_to_multipart_parts_and_context(operation)
    if opened:
        raise ValueError("Synthetic benchmark unexpectedly opened attachments")
    for header, body in encode_multipart_parts_and_context(parts, BOUNDARY):
        yield header
        if not isinstance(body, bytes):
            raise TypeError("Synthetic body must be bytes")
        yield body
        yield b"\r\n"
    yield f"--{BOUNDARY}--\r\n".encode()


def compress_chunks(chunks: Iterable[bytes]) -> bytes:
    output = BytesIO()
    with zstandard.ZstdCompressor(level=3).stream_writer(output, closefd=False) as writer:
        for chunk in chunks:
            writer.write(chunk)
    return output.getvalue()


def preconvert_models(obj: Any, cache: dict[int, Any] | None = None) -> Any:
    cache = {} if cache is None else cache
    if isinstance(obj, BaseModel):
        identity = id(obj)
        if identity not in cache:
            cache[identity] = preconvert_models(obj.model_dump(mode="json"), cache)
        return cache[identity]
    if isinstance(obj, dict):
        return {key: preconvert_models(value, cache) for key, value in obj.items()}
    if isinstance(obj, list):
        return [preconvert_models(value, cache) for value in obj]
    if isinstance(obj, tuple):
        return tuple(preconvert_models(value, cache) for value in obj)
    return obj


def merge_before_serialization(create: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    merged = create.copy()
    for key, value in patch.items():
        if value is not None:
            merged[key] = value
    return merged


def _measure(
    *,
    stage: str,
    workload: str,
    size_bytes: int,
    workers: int,
    iterations: int,
    compression: bool,
    operation: Callable[[int], tuple[int, int]],
    byte_budget: int | None = None,
    sink_state: SinkState | None = None,
) -> Measurement:
    gc.collect()
    rss_before = current_rss_bytes()
    peak_before = peak_rss_bytes()
    TraceState.dump_calls = 0
    tracemalloc.start()
    started = time.perf_counter()
    budget = WeightedSemaphore(byte_budget) if byte_budget else None
    estimated_work_bytes = size_bytes * 2 + 4096

    def invoke(sequence: int) -> tuple[int, int]:
        if budget:
            budget.acquire(estimated_work_bytes)
        try:
            return operation(sequence)
        finally:
            if budget:
                budget.release(estimated_work_bytes)

    with ThreadPoolExecutor(max_workers=workers) as executor:
        results = list(executor.map(invoke, range(iterations)))
    elapsed = time.perf_counter() - started
    _, traced_peak = tracemalloc.get_traced_memory()
    snapshot = tracemalloc.take_snapshot()
    hotspots = tuple(str(stat) for stat in snapshot.statistics("lineno")[:10])
    tracemalloc.stop()
    rss_peak = max(peak_before, peak_rss_bytes())
    input_bytes = sum(item[0] for item in results)
    output_bytes = sum(item[1] for item in results)
    gc.collect()
    rss_settled = current_rss_bytes()
    throughput = output_bytes / MIB / elapsed if elapsed else 0.0
    requests_count = sink_state.requests if sink_state else 0
    connections_count = len(sink_state.connections) if sink_state else 0
    return Measurement(
        stage=stage,
        workload=workload,
        size_bytes=size_bytes,
        workers=workers,
        iterations=iterations,
        compression=compression,
        elapsed_seconds=elapsed,
        throughput_mib_s=throughput,
        tracemalloc_peak_bytes=traced_peak,
        rss_before_bytes=rss_before,
        rss_peak_bytes=rss_peak,
        rss_settled_bytes=rss_settled,
        input_bytes=input_bytes,
        output_bytes=output_bytes,
        byte_amplification=output_bytes / input_bytes if input_bytes else 0.0,
        byte_budget_bytes=byte_budget,
        max_in_flight_bytes=budget.peak_used if budget else workers * estimated_work_bytes,
        allocation_hotspots=hotspots,
        model_dump_calls=TraceState.dump_calls,
        requests=requests_count,
        connections=connections_count,
    )


def run_case(
    stage: str,
    workload: str,
    size_bytes: int,
    workers: int,
    iterations: int,
    compression: bool,
    byte_budget: int | None,
) -> Measurement:
    runs = [make_run(size_bytes, workload, sequence) for sequence in range(iterations)]
    if stage in {"serialize", "serialize-preconverted"}:

        def operation(sequence: int) -> tuple[int, int]:
            run = runs[sequence]
            if stage == "serialize-preconverted":
                run = preconvert_models(run)
            serialized = serialize_run_dict("post", run)
            return size_bytes * 2, serialized.calculate_serialized_size()

        return _measure(
            stage=stage,
            workload=workload,
            size_bytes=size_bytes,
            workers=workers,
            iterations=iterations,
            compression=False,
            operation=operation,
            byte_budget=byte_budget,
        )

    multipart_stages = {
        "multipart",
        "streaming",
        "compression-buffered",
        "compression-streaming",
    }
    if stage in multipart_stages:

        def operation(sequence: int) -> tuple[int, int]:
            serialized = serialize_run_dict("post", runs[sequence])
            serialized_size = serialized.calculate_serialized_size()
            if stage == "multipart":
                body = drain_encoder(multipart_parts(serialized))
            elif stage == "streaming":
                body = b"".join(stream_multipart(serialized))
            elif not compression:
                body = drain_encoder(multipart_parts(serialized))
            elif stage == "compression-streaming":
                body = compress_chunks(stream_multipart(serialized))
            else:
                body = zstandard.ZstdCompressor(level=3).compress(
                    drain_encoder(multipart_parts(serialized))
                )
            return serialized_size, len(body)

        return _measure(
            stage=stage,
            workload=workload,
            size_bytes=size_bytes,
            workers=workers,
            iterations=iterations,
            compression=compression,
            operation=operation,
            byte_budget=byte_budget,
        )

    if stage in {"upload", "upload-streaming"}:
        server = SinkServer(("127.0.0.1", 0))
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()
        session = requests.Session()
        endpoint = f"http://127.0.0.1:{server.server_port}/runs/multipart"

        def operation(sequence: int) -> tuple[int, int]:
            serialized = serialize_run_dict("post", runs[sequence])
            if stage == "upload-streaming":
                wire_size = sum(len(chunk) for chunk in stream_multipart(serialized))
                data: Any = stream_multipart(serialized)
                content_type = f"multipart/form-data; boundary={BOUNDARY}"
            else:
                encoder = MultipartEncoder(multipart_parts(serialized), boundary=BOUNDARY)
                wire_size = encoder.len
                data = encoder
                content_type = encoder.content_type
            response = session.post(
                endpoint,
                data=data,
                headers={"Content-Type": content_type},
                timeout=60,
            )
            response.raise_for_status()
            return serialized.calculate_serialized_size(), wire_size

        try:
            return _measure(
                stage=stage,
                workload=workload,
                size_bytes=size_bytes,
                workers=workers,
                iterations=iterations,
                compression=False,
                operation=operation,
                byte_budget=byte_budget,
                sink_state=server.state,
            )
        finally:
            session.close()
            server.shutdown()
            server.server_close()
            server_thread.join()

    raise ValueError(f"Unknown stage: {stage}")


def parse_csv_ints(value: str) -> list[int]:
    return [int(item) for item in value.split(",") if item]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--stages",
        default=(
            "serialize,serialize-preconverted,multipart,streaming,compression-buffered,"
            "compression-streaming,upload,upload-streaming"
        ),
    )
    parser.add_argument("--sizes-mib", default="1,5,20")
    parser.add_argument("--workers", default="1,4,16,32")
    parser.add_argument("--workloads", default="dict,pydantic")
    parser.add_argument("--iterations", type=int, default=3)
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--compression", default="off,on")
    parser.add_argument("--byte-budget-mib", type=int)
    parser.add_argument("--output")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    stages = args.stages.split(",")
    sizes = [value * MIB for value in parse_csv_ints(args.sizes_mib)]
    workers_values = parse_csv_ints(args.workers)
    workloads = args.workloads.split(",")
    compression_values = [value == "on" for value in args.compression.split(",")]
    byte_budget = args.byte_budget_mib * MIB if args.byte_budget_mib else None
    output = open(args.output, "w", encoding="utf-8") if args.output else None
    try:
        for repetition in range(args.repetitions):
            for stage in stages:
                stage_compression = (
                    compression_values if stage.startswith("compression-") else [False]
                )
                for workload in workloads:
                    for size_bytes in sizes:
                        for workers in workers_values:
                            for compression in stage_compression:
                                measurement = run_case(
                                    stage,
                                    workload,
                                    size_bytes,
                                    workers,
                                    args.iterations,
                                    compression,
                                    byte_budget,
                                )
                                record = {"repetition": repetition, **asdict(measurement)}
                                line = json.dumps(record, sort_keys=True)
                                print(line, flush=True)
                                if output:
                                    output.write(line + "\n")
                                    output.flush()
    finally:
        if output:
            output.close()


if __name__ == "__main__":
    main()
