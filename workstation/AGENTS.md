# AGENTS.md

Request authentication in `src/server/auth.ts` is the only security boundary of
this whole feature. The server is published through a LangSmith tunnel that any
holder of an organization API key can reach, so the tunnel authorizes nothing,
and the backend deliberately reaches the whole filesystem, so nothing below the
signature check constrains a verified request. Every route verifies an HMAC over
the method, path, timestamp and body bytes, in constant time, and fails closed.
Signatures are single use for as long as their timestamp stays in range, so a
captured `execute` request cannot be replayed into a second run of the command.
Weakening anything in that file hands the machine over; treat it accordingly.

Paths are real host absolute paths, never deep agents virtual paths. `execute`
runs a real shell, so `pwd` and `git status` print host paths that the model
feeds straight back into `read` and `grep`; under virtual addressing every one
of those reads misses.

The backend reaches the whole filesystem, as the user does. `defaultDir` is only
where a relative path resolves from, where an `execute` with no cwd runs, and
where a search with no path starts. Do not add a containment check back: a
fenced file API alongside an unfenced shell only produces the split where the
agent can `cat` a file it is refused permission to `read`, and the fence stops
no one, since `execute` runs real commands as the user.

A failure is reported as a result `error`, never thrown, because an unreadable
path is an ordinary tool failure the agent should read and recover from.

The walk caps in `src/backend/search.ts` are load-bearing now that a `glob` can
start from `/`, and the walk must not descend into symlinked directories,
because a cycle would otherwise never terminate.

Result shapes mirror the `deepagents.backends.protocol` dataclasses field for
field, because the Python client feeds each response straight into them.
`src/server/wire.ts` is the only place the camelCase TypeScript results and the
snake_case wire format meet; a field renamed anywhere else is a `TypeError` in
the agent.

`execute` streams. The tunnel requires response headers within 30 seconds and
closes a request after an hour, so a long command must emit early and keep
emitting. `executeStream` is the primitive and `execute` drains it; do not grow
a second implementation.

A command inherits only an allowlisted environment. This process holds provider
credentials and API keys, and an agent shell command is exactly where they must
not appear.

Measured tunnel limits the package is designed against, and the harness that
re-checks them, are in `spikes/workstation-tunnel/`.
