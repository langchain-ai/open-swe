from __future__ import annotations

import json

from langsmith._internal._operations import serialize_run_dict

from scripts.langsmith_upload_benchmark import (
    compress_chunks,
    deterministic_text,
    drain_encoder,
    make_run,
    merge_before_serialization,
    multipart_parts,
    run_case,
    stream_multipart,
)


def test_deterministic_workloads_have_expected_shape() -> None:
    first = make_run(4096, "dict", 7)
    second = make_run(4096, "dict", 7)
    pydantic_run = make_run(4096, "pydantic", 7)

    assert first == second
    assert len(deterministic_text(4096, "seed").encode()) == 4096
    assert first["id"] == pydantic_run["id"]
    assert first["inputs"]["state"] == pydantic_run["inputs"]["state"].model_dump()


def test_streaming_multipart_matches_toolbelt_encoder() -> None:
    operation = serialize_run_dict("post", make_run(4096, "dict"))

    baseline = drain_encoder(multipart_parts(operation))
    prototype = b"".join(stream_multipart(operation))

    assert prototype == baseline
    assert compress_chunks([prototype]) == compress_chunks(stream_multipart(operation))


def test_merge_before_serialization_preserves_patch_semantics() -> None:
    create = {"id": "run", "name": "before", "inputs": {"value": 1}, "error": None}
    patch = {"name": "after", "inputs": None, "outputs": {"value": 2}}

    merged = merge_before_serialization(create, patch)

    assert merged == {
        "id": "run",
        "name": "after",
        "inputs": {"value": 1},
        "outputs": {"value": 2},
        "error": None,
    }
    assert create["name"] == "before"


def test_measurement_is_machine_readable_and_tracks_connection_reuse() -> None:
    measurement = run_case(
        "upload",
        workload="dict",
        size_bytes=4096,
        workers=1,
        iterations=2,
        compression=False,
        byte_budget=None,
    )

    payload = json.loads(json.dumps(measurement.__dict__))
    assert payload["requests"] == 2
    assert payload["connections"] == 1
    assert payload["output_bytes"] >= payload["input_bytes"]
