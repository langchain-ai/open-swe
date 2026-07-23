### LangSmith upload efficiency investigation

This benchmark targets `langsmith==0.9.8`, the version pinned by Open SWE. It separates run serialization, `requests-toolbelt` multipart draining, segmented multipart, buffered/streaming zstd, and buffered/streaming HTTP upload to a loopback sink. Workloads are generated before `tracemalloc` starts so payload construction does not pollute SDK allocation measurements. Raw JSONL measurements are stored beside this report.

#### Reproduce

```bash
uv run python scripts/langsmith_upload_benchmark.py \
  --sizes-mib 1,5,20 --workers 1,4,16,32 --iterations 32 --repetitions 3 \
  --workloads dict,pydantic --output results.jsonl
```

The full cross-product is intentionally expensive. Checked-in samples include three repetitions for 1 MiB at 1/4 workers, a 32-iteration concurrency sweep, 5/20 MiB samples, and a three-repetition 32-worker run with a 4 MiB byte budget. Payload text is deterministic SHAKE-256 output.

#### Corrected results

Medians from repeated 1 MiB workloads (each run has roughly 1 MiB in both inputs and outputs):

| Workload | Workers | Stage | Throughput (MiB/s) | traced peak (MiB) | `model_dump` calls/run |
|---|---:|---|---:|---:|---:|
| dict | 1 | serialize | 2,059 | 16.0 | 0 |
| pydantic | 1 | serialize | 2,627 | 16.0 | 2 |
| pydantic | 1 | pre-convert shared models | 2,108 | 16.0 | 1 |
| dict | 1 | multipart baseline | 819 | 20.1 | 0 |
| dict | 1 | segmented multipart | 2,084 | 18.0 | 0 |
| dict | 4 | multipart baseline | 434 | 60.1 | 0 |
| dict | 4 | segmented multipart | 1,315 | 36.1 | 0 |
| dict | 1 | HTTP baseline | 130 | 16.4 | 0 |
| dict | 1 | streaming HTTP | 316 | 18.2 | 0 |
| dict | 4 | HTTP baseline | 100 | 65.4 | 0 |
| dict | 4 | streaming HTTP | 274 | 69.0 | 0 |

The segmented body is byte-for-byte identical to `MultipartEncoder` in tests. In isolation it was 2.5–3× faster and reduced the four-worker traced peak by 40%. When sent through `requests` as a chunked iterable, it remained 2.4–2.8× faster but did not lower peak memory at 1/4 workers; request/socket buffering dominates that path. A native transport with vectored writes or `readinto` could preserve existing byte segments without the chunk framing and Python iterator overhead.

Serialization output was 1.002× the large input/output bytes, and multipart wire size was 1.001× serialized size. Final-size amplification is negligible even though cumulative copying is expensive. The production profile’s 11.1 GB in `MultipartEncoder.append` is therefore consistent with repeated transient copies, not a wire payload that is 11.1 GB larger.

The Pydantic workload confirms two `model_dump` calls per run because the same state object appears in inputs and outputs. A generic identity-cache prototype cut this to one but was 20% slower at one worker due to recursively copying the object graph. SDK-wide pre-conversion is not justified by this result. A narrower API that accepts already serialized fragments, or application-side removal of duplicated full state, is more promising.

Buffered and streaming zstd produced the same 0.262× wire ratio. At four workers, streaming reduced median traced peak from 80.1 to 66.3 MiB (17%) with similar throughput (68–70 MiB/s). Streaming compression is supported on memory grounds; no CPU gain is claimed.

A shared `requests.Session` reused one connection for every sequential upload. Four simultaneous workers used four connections; the 32-worker sweep used the connections needed for concurrent requests rather than creating a new connection for every sequential request. This does not reproduce platform TLS/truststore behavior, so handshake instrumentation is still needed before attributing truststore allocation volume to context creation.

#### Concurrency and byte budget

At 32 requested workers, a 4 MiB in-flight budget produced these medians:

| Stage | Unbounded peak (MiB) | Budgeted peak (MiB) | Unbounded MiB/s | Budgeted MiB/s |
|---|---:|---:|---:|---:|
| multipart | 80.2 | 20.2 | 657 | 408 |
| HTTP baseline | 519.7 | 16.6 | 50 | 124 |
| streaming HTTP | 398.0 | 18.6 | 55 | 294 |

The limiter charges approximately 2.004 MiB per synthetic run (inputs, outputs, and attachment), so a 4 MiB budget admits one operation at a time and reports that actual peak weight. Byte-bounded concurrency reduced traced peak by 75% for multipart and 95–97% for HTTP while improving upload throughput by avoiding oversubscription. Item-count scaling to 32 workers is actively harmful for large traces.

#### Upstream recommendations

1. Add a byte-weighted limiter around serialization plus upload. Default capacity should be derived from estimated serialized bytes and configured independently from queue item count. Backpressure must block/spill according to existing durability semantics, never silently drop traces.
2. Replace `MultipartEncoder.read()` with a streaming transport over existing serialized parts. The strongest end-to-end candidate combines this with the byte limiter: 32-worker streaming upload under a 4 MiB budget was ~5.4× faster and used ~95% less traced peak memory than unbounded streaming.
3. Stream zstd output into the transport. This consistently reduced compressed-path peak memory without changing wire bytes.
4. Expose queue bytes, in-flight bytes, active workers, serialized batch bytes, requests, connections, and TLS handshakes. These metrics distinguish SDK churn, network behavior, and allocator retention.
5. Do not add generic recursive Pydantic memoization based on this benchmark. Investigate accepting `orjson.Fragment`/pre-serialized fields or reducing duplicate full-state tracing instead.
6. Merge create/patch dictionaries before serialization where object forms are still available. The SDK currently parses and reserializes `_none`; the included semantic prototype establishes behavior, but production-like merge-heavy measurements are still needed before prioritizing it.

#### Interpretation boundaries

`tracemalloc` measures Python-visible allocations; RSS includes native allocator retention. `ru_maxrss` is process-global and monotonic, so raw rows also include current/settled RSS and should be read in execution order. Local HTTP removes network and TLS variance. Results quantify SDK churn but neither prove nor disprove glibc arena retention.