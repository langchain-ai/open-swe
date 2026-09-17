import assert from "node:assert/strict"
import { request as httpRequest } from "node:http"
import test, { type TestContext } from "node:test"

import type {
  Backend,
  DeleteResult,
  EditResult,
  ExecuteEvent,
  ExecuteOptions,
  ExecuteResponse,
  FileDownloadResponse,
  FileUploadResponse,
  GlobResult,
  GrepOptions,
  GrepResult,
  LsResult,
  ReadOptions,
  ReadResult,
  UploadFile,
  WriteResult,
} from "../src/backend/types.ts"
import { signRequest } from "../src/server/auth.ts"
import {
  createWorkstationServer,
  type WorkstationLogger,
  type WorkstationServerOptions,
} from "../src/server/server.ts"

const SECRET = "workstation-test-secret"
const ROOT = "/tmp/workstation-fake-root"

interface Call {
  readonly method: string
  readonly args: readonly unknown[]
}

interface Deferred {
  readonly promise: Promise<void>
  release(): void
}

function deferred(): Deferred {
  let release = (): void => {}
  const promise = new Promise<void>((resolve) => {
    release = resolve
  })
  return { promise, release }
}

class FakeBackend implements Backend {
  readonly rootDir: string
  readonly calls: Call[] = []
  lsResult: LsResult = {
    entries: [
      {
        path: `${ROOT}/a.txt`,
        isDir: false,
        size: 3,
        modifiedAt: "2026-01-02T03:04:05Z",
      },
    ],
  }
  readResult: ReadResult = {
    fileData: {
      content: "hi\n",
      encoding: "utf-8",
      modifiedAt: "2026-01-02T03:04:05Z",
    },
    totalLines: 10,
    startLine: 1,
    endLine: 2,
    nextOffset: 2,
  }
  writeResult: WriteResult = { path: `${ROOT}/a.txt` }
  editResult: EditResult = { path: `${ROOT}/a.txt`, occurrences: 2 }
  deleteResult: DeleteResult = { path: `${ROOT}/a.txt` }
  grepResult: GrepResult = {
    matches: [
      {
        path: `${ROOT}/a.txt`,
        line: 4,
        text: "needle",
        contextBefore: [{ line: 3, text: "before" }],
        contextAfter: [],
      },
    ],
    truncated: false,
  }
  globResult: GlobResult = {
    matches: [{ path: `${ROOT}/a.txt` }],
    truncated: true,
    truncationReason: "budget",
  }
  uploadResult: FileUploadResponse[] = [
    { path: `${ROOT}/x.bin` },
    { path: `${ROOT}/y.bin`, error: "permission_denied" },
  ]
  downloadResult: FileDownloadResponse[] = [
    { path: `${ROOT}/x.bin`, content: new Uint8Array([1, 2, 3]) },
    { path: `${ROOT}/missing.bin`, error: "file_not_found" },
  ]
  streamEvents: ExecuteEvent[] = [
    { type: "output", data: "first\n" },
    { type: "exit", exitCode: 0, truncated: false },
  ]
  streamGate: Deferred | null = null
  streamFailure: Error | null = null

  constructor(rootDir: string = ROOT) {
    this.rootDir = rootDir
  }

  private record(method: string, args: readonly unknown[]): void {
    this.calls.push({ method, args })
  }

  async ls(path: string): Promise<LsResult> {
    this.record("ls", [path])
    return this.lsResult
  }

  async read(filePath: string, options?: ReadOptions): Promise<ReadResult> {
    this.record("read", [filePath, options])
    return this.readResult
  }

  async write(filePath: string, content: string): Promise<WriteResult> {
    this.record("write", [filePath, content])
    return this.writeResult
  }

  async edit(
    filePath: string,
    oldString: string,
    newString: string,
    replaceAll?: boolean
  ): Promise<EditResult> {
    this.record("edit", [filePath, oldString, newString, replaceAll])
    return this.editResult
  }

  async delete(filePath: string): Promise<DeleteResult> {
    this.record("delete", [filePath])
    return this.deleteResult
  }

  async grep(pattern: string, options?: GrepOptions): Promise<GrepResult> {
    this.record("grep", [pattern, options])
    return this.grepResult
  }

  async glob(pattern: string, path?: string): Promise<GlobResult> {
    this.record("glob", [pattern, path])
    return this.globResult
  }

  async uploadFiles(
    files: readonly UploadFile[]
  ): Promise<FileUploadResponse[]> {
    this.record("uploadFiles", [files])
    return this.uploadResult
  }

  async downloadFiles(
    paths: readonly string[]
  ): Promise<FileDownloadResponse[]> {
    this.record("downloadFiles", [paths])
    return this.downloadResult
  }

  async execute(
    command: string,
    options?: ExecuteOptions
  ): Promise<ExecuteResponse> {
    this.record("execute", [command, options])
    return { output: "first\n", exitCode: 0, truncated: false }
  }

  async *executeStream(
    command: string,
    options?: ExecuteOptions
  ): AsyncGenerator<ExecuteEvent> {
    this.record("executeStream", [command, options])
    for (const event of this.streamEvents) {
      if (event.type === "exit" && this.streamGate !== null) {
        await this.streamGate.promise
      }
      yield event
    }
    if (this.streamFailure !== null) throw this.streamFailure
  }
}

interface LogLine {
  readonly level: "info" | "warn"
  readonly message: string
  readonly fields: Readonly<Record<string, unknown>>
}

interface Harness {
  readonly base: string
  readonly host: string
  readonly port: number
  readonly backend: FakeBackend
  readonly logs: readonly LogLine[]
  headers(method: string, path: string, body: string): Record<string, string>
  send(method: string, path: string, body?: unknown): Promise<Response>
}

async function start(
  t: TestContext,
  overrides: Partial<WorkstationServerOptions> = {},
  backend: FakeBackend = new FakeBackend()
): Promise<Harness> {
  const logs: LogLine[] = []
  const logger: WorkstationLogger = {
    info(message, fields) {
      logs.push({ level: "info", message, fields: { ...fields } })
    },
    warn(message, fields) {
      logs.push({ level: "warn", message, fields: { ...fields } })
    },
  }
  const server = createWorkstationServer({
    backends: [backend],
    secret: SECRET,
    port: 0,
    logger,
    ...overrides,
  })
  const address = await server.listen()
  t.after(() => server.close())

  const headers = (
    method: string,
    path: string,
    body: string
  ): Record<string, string> => {
    const timestamp = String(Math.floor(Date.now() / 1000))
    return {
      "content-type": "application/json",
      "x-workstation-timestamp": timestamp,
      "x-workstation-signature": signRequest(SECRET, {
        method,
        path,
        timestamp,
        body: new TextEncoder().encode(body),
      }),
    }
  }

  const base = `http://${address.host}:${address.port}`
  return {
    base,
    host: address.host,
    port: address.port,
    backend,
    logs,
    headers,
    async send(method, path, body) {
      const encoded = body === undefined ? "" : JSON.stringify(body)
      return await fetch(`${base}${path}`, {
        method,
        headers: headers(method, path, encoded),
        ...(method === "GET" ? {} : { body: encoded }),
      })
    },
  }
}

async function payload(response: Response): Promise<Record<string, unknown>> {
  const text = await response.text()
  return JSON.parse(text) as Record<string, unknown>
}

async function* ndjson(
  response: Response
): AsyncGenerator<Record<string, unknown>> {
  const body = response.body
  assert.ok(body !== null)
  const reader = body.getReader()
  const decoder = new TextDecoder()
  let buffered = ""
  for (;;) {
    const chunk = await reader.read()
    if (chunk.value !== undefined) {
      buffered += decoder.decode(chunk.value, { stream: true })
    }
    for (;;) {
      const newline = buffered.indexOf("\n")
      if (newline === -1) break
      const line = buffered.slice(0, newline)
      buffered = buffered.slice(newline + 1)
      if (line.length > 0) yield JSON.parse(line) as Record<string, unknown>
    }
    if (chunk.done) return
  }
}

function assertNoSecret(harness: Harness, ...texts: readonly string[]): void {
  for (const text of [...texts, JSON.stringify(harness.logs)]) {
    assert.ok(!text.includes(SECRET), "secret leaked")
  }
}

test("health reports the package version and the served roots", async (t) => {
  const harness = await start(t)
  const response = await harness.send("GET", "/v1/health")
  assert.equal(response.status, 200)
  assert.deepEqual(await payload(response), {
    ok: true,
    version: "0.1.0",
    roots: [ROOT],
  })
})

test("an unsigned request is rejected with no detail", async (t) => {
  const harness = await start(t)
  const response = await fetch(`${harness.base}/v1/health`)
  assert.equal(response.status, 401)
  const body = await response.text()
  assert.equal(body, '{"error":"unauthorized"}')
  const rejection = harness.logs.find(
    (line) => line.message === "workstation request unauthorized"
  )
  assert.equal(rejection?.fields.reason, "malformed_signature")
  assertNoSecret(harness, body)
})

test("a signature replayed on another path is rejected", async (t) => {
  const harness = await start(t)
  const body = JSON.stringify({ root: ROOT, path: ROOT })
  const response = await fetch(`${harness.base}/v1/fs/glob`, {
    method: "POST",
    headers: harness.headers("POST", "/v1/fs/ls", body),
    body,
  })
  assert.equal(response.status, 401)
  assert.equal(await response.text(), '{"error":"unauthorized"}')
  assert.equal(harness.backend.calls.length, 0)
})

test("a body tampered after signing is rejected", async (t) => {
  const harness = await start(t)
  const signedBody = JSON.stringify({ root: ROOT, path: ROOT })
  const response = await fetch(`${harness.base}/v1/fs/ls`, {
    method: "POST",
    headers: harness.headers("POST", "/v1/fs/ls", signedBody),
    body: JSON.stringify({ root: ROOT, path: `${ROOT}/elsewhere` }),
  })
  assert.equal(response.status, 401)
  assert.equal(harness.backend.calls.length, 0)
})

test("a stale timestamp is rejected", async (t) => {
  const harness = await start(t)
  const body = JSON.stringify({ root: ROOT, path: ROOT })
  const timestamp = String(Math.floor(Date.now() / 1000) - 3600)
  const response = await fetch(`${harness.base}/v1/fs/ls`, {
    method: "POST",
    headers: {
      "x-workstation-timestamp": timestamp,
      "x-workstation-signature": signRequest(SECRET, {
        method: "POST",
        path: "/v1/fs/ls",
        timestamp,
        body: new TextEncoder().encode(body),
      }),
    },
    body,
  })
  assert.equal(response.status, 401)
  const rejection = harness.logs.find(
    (line) => line.message === "workstation request unauthorized"
  )
  assert.equal(rejection?.fields.reason, "timestamp_out_of_range")
})

test("an unknown route is authenticated before it is routed", async (t) => {
  const harness = await start(t)
  const unsigned = await fetch(`${harness.base}/v1/secret-probe`, {
    method: "POST",
    body: "{}",
  })
  assert.equal(unsigned.status, 401)
  assert.equal(await unsigned.text(), '{"error":"unauthorized"}')

  const signed = await harness.send("POST", "/v1/secret-probe", {})
  assert.equal(signed.status, 404)
  assert.deepEqual(await payload(signed), { error: "not_found" })
})

test("a wrong method is rejected with the allowed method", async (t) => {
  const harness = await start(t)
  const response = await harness.send("GET", "/v1/fs/ls")
  assert.equal(response.status, 405)
  assert.equal(response.headers.get("allow"), "POST")
  assert.deepEqual(await payload(response), { error: "method_not_allowed" })

  const health = await harness.send("POST", "/v1/health", {})
  assert.equal(health.status, 405)
  assert.equal(health.headers.get("allow"), "GET")
})

test("a root no backend owns is rejected", async (t) => {
  const harness = await start(t)
  const response = await harness.send("POST", "/v1/fs/ls", {
    root: "/tmp/some-other-project",
    path: "/tmp/some-other-project",
  })
  assert.equal(response.status, 404)
  assert.deepEqual(await payload(response), { error: "unknown_root" })
  assert.equal(harness.backend.calls.length, 0)
})

test("a request reaches the backend that owns the deepest matching root", async (t) => {
  const outer = new FakeBackend("/tmp/workstation-outer")
  const inner = new FakeBackend("/tmp/workstation-outer/inner")
  const harness = await start(t, { backends: [outer, inner] }, outer)
  const response = await harness.send("POST", "/v1/fs/ls", {
    root: "/tmp/workstation-outer/inner",
    path: "/tmp/workstation-outer/inner",
  })
  assert.equal(response.status, 200)
  assert.equal(outer.calls.length, 0)
  assert.equal(inner.calls.length, 1)
})

test("a malformed body is rejected before the backend is called", async (t) => {
  const harness = await start(t)
  const cases: readonly unknown[] = [
    { root: ROOT },
    { root: ROOT, path: ROOT, extra: 1 },
    { root: ROOT, path: 7 },
    [ROOT],
  ]
  for (const body of cases) {
    const response = await harness.send("POST", "/v1/fs/ls", body)
    assert.equal(response.status, 400)
    assert.equal((await payload(response)).error, "invalid_request")
  }

  const notJson = await fetch(`${harness.base}/v1/fs/ls`, {
    method: "POST",
    headers: harness.headers("POST", "/v1/fs/ls", "not json"),
    body: "not json",
  })
  assert.equal(notJson.status, 400)
  assert.equal(harness.backend.calls.length, 0)
})

test("ls round-trips as snake_case entries", async (t) => {
  const harness = await start(t)
  const response = await harness.send("POST", "/v1/fs/ls", {
    root: ROOT,
    path: `${ROOT}/sub`,
  })
  assert.equal(response.status, 200)
  assert.deepEqual(await payload(response), {
    entries: [
      {
        path: `${ROOT}/a.txt`,
        is_dir: false,
        size: 3,
        modified_at: "2026-01-02T03:04:05Z",
      },
    ],
  })
  assert.deepEqual(harness.backend.calls, [
    { method: "ls", args: [`${ROOT}/sub`] },
  ])
})

test("read round-trips file data and a complete pagination window", async (t) => {
  const harness = await start(t)
  const response = await harness.send("POST", "/v1/fs/read", {
    root: ROOT,
    file_path: `${ROOT}/a.txt`,
    offset: 0,
    limit: 2,
  })
  assert.deepEqual(await payload(response), {
    file_data: {
      content: "hi\n",
      encoding: "utf-8",
      modified_at: "2026-01-02T03:04:05Z",
    },
    total_lines: 10,
    start_line: 1,
    end_line: 2,
    next_offset: 2,
  })
  assert.deepEqual(harness.backend.calls, [
    { method: "read", args: [`${ROOT}/a.txt`, { offset: 0, limit: 2 }] },
  ])
})

test("read never emits pagination fields without their window", async (t) => {
  const backend = new FakeBackend()
  backend.readResult = {
    fileData: { content: "hi\n", encoding: "utf-8" },
    totalLines: 10,
    nextOffset: 4,
  }
  const harness = await start(t, {}, backend)
  const response = await harness.send("POST", "/v1/fs/read", {
    root: ROOT,
    file_path: `${ROOT}/a.txt`,
  })
  assert.deepEqual(await payload(response), {
    file_data: { content: "hi\n", encoding: "utf-8" },
  })
})

test("read never emits a next_offset that disagrees with end_line", async (t) => {
  const backend = new FakeBackend()
  backend.readResult = {
    startLine: 1,
    endLine: 2,
    nextOffset: 9,
    totalLines: 1,
  }
  const harness = await start(t, {}, backend)
  const response = await harness.send("POST", "/v1/fs/read", {
    root: ROOT,
    file_path: `${ROOT}/a.txt`,
  })
  assert.deepEqual(await payload(response), { start_line: 1, end_line: 2 })
})

test("read reports an uninspected window on its own", async (t) => {
  const backend = new FakeBackend()
  backend.readResult = { noLinesRequested: true }
  const harness = await start(t, {}, backend)
  const response = await harness.send("POST", "/v1/fs/read", {
    root: ROOT,
    file_path: `${ROOT}/a.txt`,
    limit: 0,
  })
  assert.deepEqual(await payload(response), { no_lines_requested: true })
})

test("write and delete round-trip the written path", async (t) => {
  const harness = await start(t)
  const written = await harness.send("POST", "/v1/fs/write", {
    root: ROOT,
    file_path: `${ROOT}/a.txt`,
    content: "body",
  })
  assert.deepEqual(await payload(written), { path: `${ROOT}/a.txt` })

  const deleted = await harness.send("POST", "/v1/fs/delete", {
    root: ROOT,
    file_path: `${ROOT}/a.txt`,
  })
  assert.deepEqual(await payload(deleted), { path: `${ROOT}/a.txt` })
  assert.deepEqual(harness.backend.calls, [
    { method: "write", args: [`${ROOT}/a.txt`, "body"] },
    { method: "delete", args: [`${ROOT}/a.txt`] },
  ])
})

test("edit forwards replace_all and returns the occurrence count", async (t) => {
  const harness = await start(t)
  const response = await harness.send("POST", "/v1/fs/edit", {
    root: ROOT,
    file_path: `${ROOT}/a.txt`,
    old_string: "a",
    new_string: "b",
    replace_all: true,
  })
  assert.deepEqual(await payload(response), {
    path: `${ROOT}/a.txt`,
    occurrences: 2,
  })
  assert.deepEqual(harness.backend.calls, [
    { method: "edit", args: [`${ROOT}/a.txt`, "a", "b", true] },
  ])
})

test("an error result round-trips as the error field", async (t) => {
  const backend = new FakeBackend()
  backend.writeResult = { error: "Error: permission denied" }
  const harness = await start(t, {}, backend)
  const response = await harness.send("POST", "/v1/fs/write", {
    root: ROOT,
    file_path: `${ROOT}/a.txt`,
    content: "body",
  })
  assert.equal(response.status, 200)
  assert.deepEqual(await payload(response), {
    error: "Error: permission denied",
  })
})

test("grep round-trips matches with context and forwards its options", async (t) => {
  const harness = await start(t)
  const response = await harness.send("POST", "/v1/fs/grep", {
    root: ROOT,
    pattern: "needle",
    path: `${ROOT}/sub`,
    glob: "*.txt",
    max_count: 5,
    context_lines: 1,
  })
  assert.deepEqual(await payload(response), {
    matches: [
      {
        path: `${ROOT}/a.txt`,
        line: 4,
        text: "needle",
        context_before: [{ line: 3, text: "before" }],
        context_after: [],
      },
    ],
    truncated: false,
  })
  assert.deepEqual(harness.backend.calls, [
    {
      method: "grep",
      args: [
        "needle",
        {
          path: `${ROOT}/sub`,
          glob: "*.txt",
          maxCount: 5,
          contextLines: 1,
        },
      ],
    },
  ])
})

test("glob round-trips the truncation reason", async (t) => {
  const harness = await start(t)
  const response = await harness.send("POST", "/v1/fs/glob", {
    root: ROOT,
    pattern: "**/*.txt",
  })
  assert.deepEqual(await payload(response), {
    matches: [{ path: `${ROOT}/a.txt` }],
    truncated: true,
    truncation_reason: "budget",
  })
  assert.deepEqual(harness.backend.calls, [
    { method: "glob", args: ["**/*.txt", undefined] },
  ])
})

test("upload decodes base64 content and reports per-file errors", async (t) => {
  const harness = await start(t)
  const response = await harness.send("POST", "/v1/fs/upload", {
    root: ROOT,
    files: [
      { path: `${ROOT}/x.bin`, content_base64: "AQID" },
      { path: `${ROOT}/y.bin`, content_base64: "" },
    ],
  })
  assert.deepEqual(await payload(response), {
    files: [
      { path: `${ROOT}/x.bin` },
      { path: `${ROOT}/y.bin`, error: "permission_denied" },
    ],
  })
  assert.deepEqual(harness.backend.calls, [
    {
      method: "uploadFiles",
      args: [
        [
          { path: `${ROOT}/x.bin`, content: new Uint8Array([1, 2, 3]) },
          { path: `${ROOT}/y.bin`, content: new Uint8Array([]) },
        ],
      ],
    },
  ])
})

test("upload rejects content that is not base64", async (t) => {
  const harness = await start(t)
  const response = await harness.send("POST", "/v1/fs/upload", {
    root: ROOT,
    files: [{ path: `${ROOT}/x.bin`, content_base64: "not base64!" }],
  })
  assert.equal(response.status, 400)
  assert.equal(harness.backend.calls.length, 0)
})

test("download base64-encodes content and reports per-path errors", async (t) => {
  const harness = await start(t)
  const response = await harness.send("POST", "/v1/fs/download", {
    root: ROOT,
    paths: [`${ROOT}/x.bin`, `${ROOT}/missing.bin`],
  })
  assert.deepEqual(await payload(response), {
    files: [
      { path: `${ROOT}/x.bin`, content_base64: "AQID" },
      { path: `${ROOT}/missing.bin`, error: "file_not_found" },
    ],
  })
})

test(
  "execute streams an event before the command finishes",
  { timeout: 2000 },
  async (t) => {
    const backend = new FakeBackend()
    backend.streamGate = deferred()
    const harness = await start(t, {}, backend)
    const response = await harness.send("POST", "/v1/execute", {
      root: ROOT,
      command: "echo first",
      cwd: `${ROOT}/sub`,
      timeout_seconds: 30,
      max_output_bytes: 4096,
    })
    assert.equal(response.status, 200)
    assert.equal(response.headers.get("content-type"), "application/x-ndjson")

    const events = ndjson(response)
    const first = await events.next()
    assert.deepEqual(first.value, { type: "output", data: "first\n" })

    backend.streamGate.release()
    const rest: Record<string, unknown>[] = []
    for await (const event of events) rest.push(event)
    assert.deepEqual(rest.at(-1), {
      type: "exit",
      exit_code: 0,
      truncated: false,
    })
    assert.deepEqual(harness.backend.calls, [
      {
        method: "executeStream",
        args: [
          "echo first",
          { cwd: `${ROOT}/sub`, timeoutSeconds: 30, maxOutputBytes: 4096 },
        ],
      },
    ])
  }
)

test("execute keeps a silent stream alive", { timeout: 2000 }, async (t) => {
  const backend = new FakeBackend()
  backend.streamGate = deferred()
  const harness = await start(t, { keepaliveIntervalMs: 25 }, backend)
  const response = await harness.send("POST", "/v1/execute", {
    root: ROOT,
    command: "sleep 1",
  })

  const events = ndjson(response)
  assert.deepEqual((await events.next()).value, {
    type: "output",
    data: "first\n",
  })

  for (;;) {
    const event = await events.next()
    assert.equal(event.done, false)
    if (event.value?.type === "exit") {
      assert.fail("the exit event arrived before any keepalive")
    }
    if (event.value?.type === "keepalive") break
  }

  backend.streamGate.release()
  const rest: Record<string, unknown>[] = []
  for await (const event of events) rest.push(event)
  assert.deepEqual(rest.at(-1), {
    type: "exit",
    exit_code: 0,
    truncated: false,
  })
})

test(
  "a stream that fails mid-command reports a terminal error event",
  { timeout: 2000 },
  async (t) => {
    const backend = new FakeBackend()
    backend.streamEvents = [{ type: "output", data: "first\n" }]
    backend.streamFailure = new Error("pty exploded")
    const harness = await start(t, {}, backend)
    const response = await harness.send("POST", "/v1/execute", {
      root: ROOT,
      command: "true",
    })
    const events: Record<string, unknown>[] = []
    for await (const event of ndjson(response)) events.push(event)
    assert.deepEqual(events.at(-1), {
      type: "error",
      message: "execute stream failed",
    })
    const logged = harness.logs.find(
      (line) => line.message === "workstation execute stream failed"
    )
    assert.equal(logged?.fields.error, "pty exploded")
  }
)

test("a declared body over the cap is rejected", async (t) => {
  const harness = await start(t, { maxBodyBytes: 512 })
  const body = JSON.stringify({ root: ROOT, path: "x".repeat(600) })
  const response = await fetch(`${harness.base}/v1/fs/ls`, {
    method: "POST",
    headers: harness.headers("POST", "/v1/fs/ls", body),
    body,
  })
  assert.equal(response.status, 413)
  assert.deepEqual(await payload(response), { error: "payload_too_large" })
  assert.equal(harness.backend.calls.length, 0)
})

test(
  "a body over the cap is rejected without being buffered",
  { timeout: 2000 },
  async (t) => {
    const harness = await start(t, { maxBodyBytes: 1024 })
    const clientErrors: Error[] = []
    const chunk = "x".repeat(512)
    const status = await new Promise<number>((resolve, reject) => {
      const client = httpRequest(
        {
          host: harness.host,
          port: harness.port,
          method: "POST",
          path: "/v1/fs/ls",
          headers: {
            ...harness.headers("POST", "/v1/fs/ls", "{}"),
            "transfer-encoding": "chunked",
          },
        },
        (response) => {
          resolve(response.statusCode ?? 0)
          response.resume()
        }
      )
      client.on("error", (error) => {
        clientErrors.push(error)
        if (clientErrors.length > 8) reject(error)
      })
      const pump = (): void => {
        if (client.destroyed || client.writableEnded) return
        client.write(chunk, () => setTimeout(pump, 1))
      }
      pump()
    })
    assert.equal(status, 413)
    assert.equal(harness.backend.calls.length, 0)
    const logged = harness.logs.find(
      (line) => line.message === "workstation request body too large"
    )
    assert.equal(logged?.fields.maxBodyBytes, 1024)
  }
)

test("the server binds loopback by default", async (t) => {
  const harness = await start(t)
  assert.equal(harness.host, "127.0.0.1")
})

test("a non-loopback bind is refused unless it is explicitly allowed", async () => {
  const backend = new FakeBackend()
  const refused = createWorkstationServer({
    backends: [backend],
    secret: SECRET,
    host: "0.0.0.0",
    port: 0,
  })
  await assert.rejects(refused.listen(), /non-loopback/)
  assert.equal(refused.address, null)

  const allowed = createWorkstationServer({
    backends: [backend],
    secret: SECRET,
    host: "192.0.2.1",
    port: 0,
    allowNonLoopbackHost: true,
  })
  await assert.rejects(allowed.listen(), (error: Error) => {
    assert.doesNotMatch(error.message, /non-loopback/)
    return true
  })
  await allowed.close()
})

test("createWorkstationServer refuses an unusable configuration", () => {
  const backend = new FakeBackend()
  assert.throws(
    () => createWorkstationServer({ backends: [backend], secret: "" }),
    /non-empty secret/
  )
  assert.throws(
    () => createWorkstationServer({ backends: [], secret: SECRET }),
    /at least one backend/
  )
  assert.throws(
    () =>
      createWorkstationServer({
        backends: [backend],
        secret: SECRET,
        maxBodyBytes: 0,
      }),
    /maxBodyBytes/
  )
})

test("the secret never reaches a log line or a response body", async (t) => {
  const harness = await start(t)
  const ok = await harness.send("POST", "/v1/fs/ls", {
    root: ROOT,
    path: ROOT,
  })
  const rejected = await fetch(`${harness.base}/v1/fs/ls`, {
    method: "POST",
    headers: { "x-workstation-signature": "a".repeat(64) },
    body: "{}",
  })
  assertNoSecret(harness, await ok.text(), await rejected.text())
})

test("a captured signature cannot be replayed inside its skew window", async (t) => {
  const harness = await start(t)
  const body = JSON.stringify({ root: ROOT, path: "/" })
  const headers = harness.headers("POST", "/v1/fs/ls", body)

  const first = await fetch(`${harness.base}/v1/fs/ls`, {
    method: "POST",
    headers,
    body,
  })
  assert.equal(first.status, 200)

  const replayed = await fetch(`${harness.base}/v1/fs/ls`, {
    method: "POST",
    headers,
    body,
  })
  assert.equal(replayed.status, 401)
  assert.deepEqual(await replayed.json(), { error: "unauthorized" })
  assert.equal(
    harness.logs.filter((line) => line.fields?.reason === "signature_replayed")
      .length,
    1
  )
  assert.equal(
    harness.backend.calls.filter((call) => call.method === "ls").length,
    1
  )
})
