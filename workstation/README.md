# open-swe-workstation

Exposes directories on this machine as [deep agents](https://github.com/langchain-ai/deepagents)
backends over an authenticated HTTP API, so a cloud Open SWE thread can run
against a developer's real checkout instead of a sandbox.

Nothing here opens a port to the internet. The desktop app publishes the server
through a LangSmith tunnel, and the cloud agent reaches it from whichever
replica happens to run the thread.

## The backend

```ts
import { createLocalBackend } from "open-swe-workstation"

const backend = await createLocalBackend({ rootDir: "/Users/me/project" })

await backend.read("/Users/me/project/src/main.ts", { limit: 50 })
await backend.grep("TODO", { glob: "**/*.ts", maxCount: 20 })
await backend.execute("pnpm test", { timeoutSeconds: 600 })

for await (const event of backend.executeStream("pnpm build")) {
  if (event.type === "output") process.stdout.write(event.data)
}
```

`createLocalBackend` resolves and checks the root immediately, so a project that
has been moved or deleted fails at registration rather than mid-run. Results
mirror the `deepagents.backends.protocol` dataclasses field for field.

## Paths

Paths are **real host absolute paths**, not deep agents virtual paths. A
workstation runs a real shell, so `pwd` and `git status` print host paths that
the model feeds straight back into `read` and `grep`; virtual addressing would
make every one of those reads miss.

Relative paths resolve against the root. Anything resolving outside the root,
including through a symlink, is refused as a result `error`. That is stricter
than `FilesystemBackend` with `virtual_mode=False`, which applies no
containment at all, and the strictness is deliberate: the tunnel makes this
server reachable by every holder of an organization API key.

Containment is not a sandbox. `execute` runs real commands as the user, so it
can reach anything the user can. Containment stops the file API from wandering
outside the project and makes a request naming another project fail loudly.

## The server

```ts
import { createWorkstationServer } from "open-swe-workstation"

const server = createWorkstationServer({
  backends: [backend],
  secret: process.env.WORKSTATION_SECRET ?? "",
})
const { host, port } = await server.listen()
```

Binds `127.0.0.1:8787` by default and refuses a non-loopback bind unless
`allowNonLoopbackHost` is set.

### Authentication

The tunnel authorizes nothing, so every route requires a signature. Sign the
method, the request target, a timestamp and a hash of the exact body bytes:

```
HMAC-SHA256(secret, "v1\n" + METHOD + "\n" + TARGET + "\n" + UNIX_SECONDS + "\n" + sha256_hex(body))
```

sent as `x-workstation-signature` (lowercase hex) and `x-workstation-timestamp`
(Unix seconds). From Python:

```python
message = "\n".join(["v1", method.upper(), target, timestamp, hashlib.sha256(body).hexdigest()])
signature = hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()
```

A timestamp more than 300 seconds from the server's clock is rejected, and a
signature is accepted only once, so a captured `execute` request cannot be
replayed into a second run. Failures return `401 {"error":"unauthorized"}` with
no detail about which check failed.

### Routes

Bodies are JSON with snake_case fields matching the Python method parameters,
so a response feeds straight into its dataclass. Every `/v1/fs/*` and
`/v1/execute` body carries `root`, the absolute path of the backend to use.

| Route | Request | Response |
|---|---|---|
| `GET /v1/health` | | `{ok, version, roots[]}` |
| `POST /v1/fs/ls` | `path` | `{error?, entries?}` |
| `POST /v1/fs/read` | `file_path, offset?, limit?` | `{error?, file_data?, total_lines?, start_line?, end_line?, next_offset?, no_lines_requested?}` |
| `POST /v1/fs/write` | `file_path, content` | `{error?, path?}` |
| `POST /v1/fs/edit` | `file_path, old_string, new_string, replace_all?` | `{error?, path?, occurrences?}` |
| `POST /v1/fs/delete` | `file_path` | `{error?, path?}` |
| `POST /v1/fs/grep` | `pattern, path?, glob?, max_count?, context_lines?` | `{error?, matches?, truncated}` |
| `POST /v1/fs/glob` | `pattern, path?` | `{error?, matches?, truncated, truncation_reason?}` |
| `POST /v1/fs/upload` | `files[{path, content_base64}]` | `{files[{path, error?}]}` |
| `POST /v1/fs/download` | `paths[]` | `{files[{path, content_base64?, error?}]}` |
| `POST /v1/execute` | `command, cwd?, timeout_seconds?, max_output_bytes?` | NDJSON stream |

Optional response keys are omitted rather than nulled, so the dataclass
defaults apply, and `read`'s pagination fields are only ever emitted as a
combination its `__post_init__` accepts.

`execute` streams newline-delimited JSON: `{"type":"output","data"}`,
`{"type":"keepalive"}` while the command is quiet, and a final
`{"type":"exit","exit_code","truncated"}`. A `{"type":"error","message"}` line,
or a stream that ends with no exit event, means the command failed. The client
aggregates output and exit into one `ExecuteResponse`.

`env` is deliberately not accepted from the wire. Letting a remote caller set
`PATH` or `DYLD_INSERT_LIBRARIES` for a local shell is avoidable attack
surface. A command inherits only `HOME`, `LANG`, `LC_ALL`, `PATH`, `SHELL` and
`TMPDIR`, so this process's provider credentials never reach an agent shell.

### Tunnel limits the API is shaped around

Measured against the production edge; the harness is in
`spikes/workstation-tunnel/`.

| Limit | Value |
|---|---|
| Request body | 32 MiB, and one byte more drops the connection with no response |
| Response headers | must begin within 30 s |
| Request duration | 1 h |
| Throughput | ~0.42 MiB/s |

Streaming and WebSocket upgrades both work. The throughput figure is why bulk
file transfer is chunked and capped rather than streamed in one request.

## Development

```bash
pnpm --dir workstation run check   # typecheck + tests
pnpm --dir workstation run build   # emit dist/
```

Tests run on Node's TypeScript type-stripping, so relative imports carry a
`.ts` extension and the package compiles with `rewriteRelativeImportExtensions`.
