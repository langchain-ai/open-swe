# openswe-transcript

Open SWE's live transcript stream, shared by the backend and the runtimes that host its agents elsewhere, such as Managed Deep Agents.

A thread's transcript is an append-only event log in the backend's PostgreSQL (`openswe/transcript/`). A run hosted on another runtime hands each phase of its loop to the backend through its hooks, so the backend records its turn, completed messages and tool calls itself. The model call is the exception: it streams on the runtime, so the backend never sees its deltas. This package carries them across.

- `stream`: turns a model call's streaming deltas into fragments in paragraph mode, and tracks the subagent namespace. The backend's `TranscriptMiddleware` uses it too.
- `wire`: what a runtime sends the backend. That is `FragmentBatch` bodies posted to `PATH`, plus the id a reply was streamed under, which travels in the reply's `response_metadata` (`mark_streamed`).
- `remote`: the runtime side. `RemoteTranscriptMiddleware` captures each reply, and `HttpFragmentSink` posts its fragments with the run's token.

## Using it from an agent runtime

Depend on the package by path (`uv add ../libs/openswe-transcript`), then add the middleware to the agent and to every subagent. Put it inside the middleware that hands each reply to the backend, so a reply's fragments are flushed before the backend completes the message:

```python
from openswe_transcript.remote import HttpFragmentSink, RemoteTranscriptMiddleware

transcript = RemoteTranscriptMiddleware(HttpFragmentSink(backend_url, run_token))

define_deep_agent(
    ...,
    middleware=[BackendRunMiddleware(...), transcript, ...],
    subagents=[{..., "middleware": [transcript, ...]}],
)
```

Fragments go to the open turn of the run that `current_run_id()` names, which is the id the run's hooks report to the backend. The backend refuses fragments when the run has no open turn, and when a fragment names a message that isn't an AI message of that turn. After a refusal, the sink stops streaming for the run. Streaming is best effort: `message.completed` replaces whatever streamed, so a lost fragment heals once its message completes.
