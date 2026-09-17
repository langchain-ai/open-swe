# Workstation tunnel spike

Verification harness for carrying Open SWE agent traffic to a developer's
machine over a [LangSmith Tunnel](https://github.com/langchain-ai/langchainplus/tree/main/smith-go/connections).
It is not part of the product build. Keep it working: the measurements below are
the constraints the `workstation/` package is designed against, and they need
re-checking whenever the tunnel edge changes.

## Measured behavior

Run against the production edge on 2026-09-17.

| Property | Result |
|---|---|
| Streamed response held open | 300 s, headers immediate, keepalives every 10 s |
| Request body limit | exactly 32 MiB; one byte more drops the connection with no HTTP response |
| Throughput | ~0.42 MiB/s in both directions, so a 30 MiB transfer takes ~70 s |
| Response header deadline | 30 s |
| Max request duration | 1 h |
| WebSocket upgrade | works |
| Connector restart | reachable again 3.7 s after SIGKILL; an immediate 503 while down |

Two findings that are easy to get wrong:

- A target must be registered as a **URL**, `workstation=http://127.0.0.1:47000`.
  A bare `host:port` registers as a TCP target and every HTTP request to it 404s.
- An oversize body produces a connection reset rather than a 413, so a client
  has to treat a mid-upload read error as "too large" and cap itself.

## Running it

The connector binary is built from the LangSmith monorepo and deliberately not
committed. Build it into this directory from a worktree of that repo:

```bash
git -C ~/langchain/langchainplus worktree add ~/worktrees/langchainplus-tunnel-spike main
cd ~/worktrees/langchainplus-tunnel-spike/smith-go
CGO_ENABLED=0 go build -o <this-dir>/langsmith-connector ./cmd/langsmith-connector
```

Then, with `LANGSMITH_API_KEY` exported:

```bash
node server.mjs &                # loopback target under test
uv run python spike.py           # creates a connection, runs the tests, deletes it
uv run python oversize.py        # probes the body-size boundary
```

`spike.py` creates connections named `workstation-spike-<uuid>` and deletes
them in a `finally` block. Check `GET /v1/connections` for leftovers if it is
killed mid-run.
