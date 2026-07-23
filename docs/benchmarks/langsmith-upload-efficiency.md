### LangSmith upload efficiency investigation

This benchmark targets `langsmith==0.9.8`, the version pinned by Open SWE. It separates run serialization, `requests-toolbelt` multipart draining, a segmented multipart prototype, zstd compression, and HTTP upload to a loopback sink. Raw JSONL measurements are stored beside this report.

#### Reproduce

```bash
uv run python scripts/langsmith_upload_benchmark.py \
  --sizes-mib 1,5,20 --workers 1,4,16,32 --iterations 32 --repetitions 3 \
  --workloads dict,pydantic --output results.jsonl
```

The full cross-product is intentionally expensive. The checked-in samples use three repetitions for the 1 MiB/1- and 4-worker cases, one 32-iteration 1 MiB concurrency sweep, four iterations for 5 MiB, and two iterations for 20 MiB. Payload text is deterministic SHAKE-256 output, avoiding unrealistically compressible repeated strings.

#### Results

Medians from the repeated 1 MiB workload:

| Workload | Workers | Stage | Throughput (MiB/s) | traced peak (MiB) |
|---|---:|---|---:|---:|
| dict | 1 | serialize | 463.7 | 17.0 |
| dict | 1 | multipart baseline | 239.2 | 20.1 |
| dict | 1 | segmented multipart prototype | 362.7 | 18.0 |
| dict | 4 | multipart baseline | 256.6 | 60.1 |
| dict | 4 | segmented multipart prototype | 360.3 | 53.1 |
| pydantic | 1 | serialize | 474.8 | 17.0 |
| pydantic | 1 | upload | 99.3 | 17.2 |
| pydantic | 4 | upload | 83.5 | 66.1 |

The segmented prototype produced byte-for-byte identical multipart bodies in tests. At four workers it reduced the median traced peak from 60.1 MiB to 53.1 MiB (12%) and increased throughput from 256.6 to 360.3 MiB/s (40%). The benchmark still joins prototype segments to validate the final body; an HTTP transport that consumes segments directly should avoid that final allocation too.

Serialization generated approximately the same bytes as its large inputs and outputs (1.002× measured amplification). Multipart wire size was 1.001× serialized size. These ratios do not describe transient allocation churn: `MultipartEncoder.read()` repeatedly copies existing parts into return buffers even though final wire size barely grows.

Pydantic and dictionary serialization were within 3% in the one-worker stage, so `model_dump` was not the dominant cost for this workload. Production models with more validators may differ, so the harness keeps the Pydantic workload separate.

Whole-body and streaming zstd both produced a 0.262× wire ratio. At four workers, streaming compression reduced the median traced peak from 80.2 MiB to 66.6 MiB (17%), but throughput was within run-to-run noise and slightly lower in this sample. This supports streaming for peak-memory reduction, not a CPU-performance claim.

A shared `requests.Session` reused one connection for all sequential uploads in every repeated run. Four simultaneous workers used four connections, as expected; the 32-worker sweep used 26 connections for 32 requests. This contradicts a blanket “new session per upload” diagnosis. It does not reproduce platform truststore or TLS behavior because the sink is local HTTP.

#### Recommendations for the SDK

1. Replace `requests_toolbelt.MultipartEncoder.read()` with a transport that forwards multipart headers and existing serialized byte strings incrementally. The prototype shows the strongest measured improvement and preserves exact wire bytes.
2. Bound serialization and upload by in-flight bytes as well as queue item count. Peak traced memory scaled with concurrent multipart/upload work; the harness includes `--byte-budget-mib` and reports `max_in_flight_bytes` for evaluating limits without dropping traces.
3. Stream zstd output when compression is enabled. It reduced peak traced memory in this workload, but should be accepted on memory rather than throughput grounds.
4. Merge create/patch dictionaries before serialization where both operations are still available as objects. The SDK currently parses and reserializes `_none` when combining operations. The included semantic prototype demonstrates the intended merge, but this profile did not isolate enough merge traffic to claim an end-to-end gain.
5. Add SDK metrics for queue bytes, active workers, in-flight serialized bytes, batch bytes, requests, connections, and TLS handshakes. Connection instrumentation is necessary before attributing truststore allocation volume to per-request context creation.

#### Interpretation boundaries

`tracemalloc` measures Python-visible allocations, while RSS includes native allocator retention. `ru_maxrss` is process-global and monotonic, so raw rows also include current and settled RSS and should be interpreted in execution order. Local HTTP removes network and TLS variance. These results identify SDK allocation churn; they neither prove nor disprove glibc arena retention in production.