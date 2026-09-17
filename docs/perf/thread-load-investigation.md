# Cold thread_load investigation (2026-09-17)

Prod thread: 898d0b58-b01d-5172-bcd3-84bfea8a3074 (368 messages, 1.04 MB state), deployment
open-swe-v3 (LangSmith). Exported with LANGSMITH_API_KEY_PROD to the session scratchpad and
seeded locally with `seed_prod_thread.py <thread.json> <state.json> johannes117`.

## Prod measurements (curl, x-api-key, from laptop)

| Request | TTFB | Bytes |
|---|---|---|
| GET /threads/{id}/state | 2.4–4.0 s | 1.02 MB |
| GET /threads/{id} (row incl. values) | 0.28 s | 1.02 MB |
| POST /threads/{id}/history limit=1 | 1.1–2.0 s | 1.02 MB |
| POST /threads/{id}/history limit=20 | 34–37 s | 20.4 MB |
| GET /state, 43 KB thread | 0.68–1.1 s | 43 KB |
| GET /threads/{id}, 43 KB thread | 0.1 s | 43 KB |

`get_state` has a ~0.5–1 s fixed cost (graph factory + checkpointer path) plus a size-dependent
cost; the thread row has neither. The SDK's StreamController fires the history page after every
hydrate (limit 20, proxy clamps to 5): 5 full copies of the state per thread open.

## Local patches

- `agent/threads/transcript_state.py` + `?view=transcript&messages_limit=N` on the dashboard
  state proxy; `GET …/state/tool-results?ids=` for deferred results.
- langgraph-api (.venv site-packages): `langgraph_api_transcript_state.patch`
  (`GET /threads/{id}/state?view=transcript&messages_limit=N`, served from the thread row while
  idle; `X-State-Source` header). Originals in `langgraph_api_orig/`.
- langgraphjs: `patches/@langchain__langgraph-sdk@1.11.0.patch` (StreamController `getState`
  and `discoverHistory` options) and `patches/@langchain__react@1.1.0.patch` (pass-through).
  After changing patches, delete `ui/node_modules/.vite/deps` or Vite keeps the old bundle.

## Local results (langgraph dev, Vite dev server, client navigation, hidden pane)

| Mode | state bytes | state TTFB | ready |
|---|---|---|---|
| full | 1,015,995 | 168 ms | 420 ms |
| transcript | 205,791 | 50 ms | 272 ms |
| transcript, history=0 | 205,791 | 50 ms | 330 ms |
| transcript, limit=50 | 22,135 | 50 ms | 255 ms |

Local `get_state` is 45 ms, so the prod fixed cost cannot be reproduced here; the transcript view
skips that path entirely (`row_state` phase 0 ms). The state request starts ~200 ms after the
navigation in every mode: client-side gap worth its own look.

Measure with a dev session: `issue_session(login=..., email=..., avatar_url=None, user_id=<uuid>)`
from `agent.dashboard.oauth`, cookie `osw_session`, then `?perf=1&view=…&history=…`.

## Postgres runtime locally (`langgraph up`, 2026-09-17)

The platform Postgres runtime only ships inside `langchain/langgraph-api`, so:

```
langgraph up -c langgraph.postgres.json -d docker-compose.postgres-override.yml --port 8123 --no-pull --wait \
  --postgres-uri "postgres://postgres:postgres@host.docker.internal:5433/langgraph?sslmode=disable"
```

`langgraph.postgres.json` is `langgraph.json` minus the dashboard `dockerfile_lines`, pinned to
api_version 0.13.4 (the venv's version, so the patched `state.py` / `api/threads.py` can be
volume-mounted over the image's bytecode-only modules; the override file does that with absolute
paths, and `.py` beats sourceless `.pyc`). Both files are git-excluded via `.git/info/exclude`.
Redis comes with it; Postgres is the existing `open-swe-postgres` container (database `langgraph`).
Seed: `LANGGRAPH_SEED_URL=http://localhost:8123 python seed_prod_thread.py …` (1 checkpoint) and
`seed_incremental.py … 898d0b58-b01d-5172-bcd3-84bfea8a3075` (369 checkpoints, 37 s).

| Postgres runtime, 369-checkpoint thread | TTFB | Bytes |
|---|---|---|
| GET /threads/{id} | 25 ms | 1.02 MB |
| GET /threads/{id}/state | 45–105 ms | 1.02 MB |
| GET /threads/{id}/state?view=transcript (patched, from row) | 23–30 ms | 206 KB |
| … &messages_limit=50 | 22–33 ms | 22 KB |
| POST /history limit=5 | 150 ms | 5.1 MB |

Prod at the same time: `/assistants/agent/graph` (factory only) +50 ms over baseline; small thread
`/state` 300–360 ms vs row 105–160 ms; large thread `/state` 0.76–4 s vs row 0.28 s. The prod gap is
size-dependent and variable and does not reproduce locally at any checkpoint count; the row read is
consistently the cheaper path everywhere, which is what the transcript view uses.


## 2026-09-17, later: `messages_limit` removed

Johannes decided a message cap brings other problems and only wants the transcript view to be fast, so the `messages_limit` parameter was removed everywhere (proxy, langgraph-api patch, UI flag). The rows above that mention it are historical.

## 2026-09-17, later: flags hardcoded

`?view=` / `?history=` URL flags and their `localStorage` persistence were removed. The switches
are constants in `ui/src/features/agents/lib/stream/hydrationFlags.ts` (`HYDRATION_FLAGS`), so a
dev build behaves the same on every load; edit the constant to compare modes.

## 2026-09-17, later: running threads hydrate from the event stream

Idea: the run worker publishes a full `values` event after every step into the thread's event
stream (the tape `/stream/events` replays). For a busy thread, `GET /threads/{id}/state?view=transcript`
(langgraph-api patch) now replays that tape server-side through a `ThreadRunManager` with an
in-process sink, keeps the last root `values` event and the highest protocol `seq`, stops after a
quiet gap (0.2 s, min 0.3 s, cap 3 s), and returns the values plus
`metadata.open_swe_transcript.stream_since=<seq>` and `X-State-Source: stream`.

SDK patch (`patches/@langchain__langgraph-sdk@1.11.0.patch`): `ThreadStream.setInitialSince(seq)`
applies `since` to the first shared-stream open only; `StreamController.hydrate` reads
`stream_since` from the hydration payload and calls it before the root pump opens. Result: the
transcript paints the current state once, and the live subscription starts at the live edge
instead of replaying the run from seq 0. Later rotations (a subagent panel opening) still replay
per the SDK's contract.

Proxy: busy threads in transcript view call the upstream `?view=transcript` and use it when
`X-State-Source` is present, else fall back to `get_state`. The container mounts `agent/` so
proxy edits only need a container recreate.

Measured on the Postgres runtime during a streaming run: `x-state-source: stream`, 373 messages,
236 KB, TTFB 0.33–0.9 s (replay + quiet wait), `stream_since` advancing 22 → 34 as steps completed.
Known gaps: no `step` comes through (no root `checkpoints` events in this run), so the seed has no
checkpoint step; the quiet-window heuristic is not a true live-edge signal; an untested run of
`/dashboard/api` requests logged a TimeoutError traceback in the container worth reading.

Container-side test run: `POST http://localhost:8123/threads/<id>/runs` with
`{"assistant_id":"agent","input":{"messages":[{"type":"human","content":"..."}]},"stream_mode":["values","updates","messages-tuple","checkpoints"],"stream_resumable":true}`
("events" is rejected by the runs schema there).
