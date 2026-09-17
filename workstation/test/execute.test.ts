import assert from "node:assert/strict"
import { mkdtemp, rm, stat } from "node:fs/promises"
import { tmpdir } from "node:os"
import path from "node:path"
import test from "node:test"
import type { TestContext } from "node:test"

import type { ExecuteConfig } from "../src/backend/execute.ts"
import { execute, executeStream } from "../src/backend/execute.ts"
import type { ExecuteEvent } from "../src/backend/types.ts"

const BASH: ExecuteConfig = { shell: "/bin/bash" }

async function makeRoot(t: TestContext): Promise<string> {
  const dir = await mkdtemp(path.join(tmpdir(), "workstation-execute-"))
  t.after(() => rm(dir, { recursive: true, force: true }))
  return dir
}

function delay(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

async function collect(
  stream: AsyncIterable<ExecuteEvent>
): Promise<ExecuteEvent[]> {
  const events: ExecuteEvent[] = []
  for await (const event of stream) events.push(event)
  return events
}

function isRunning(pid: number): boolean {
  try {
    process.kill(pid, 0)
    return true
  } catch (error) {
    const code = (error as NodeJS.ErrnoException).code
    if (code === "ESRCH") return false
    throw error
  }
}

async function waitForDeath(pid: number): Promise<boolean> {
  for (let attempt = 0; attempt < 40; attempt += 1) {
    if (!isRunning(pid)) return true
    await delay(25)
  }
  return false
}

function firstPid(events: readonly ExecuteEvent[]): number {
  const output = events
    .filter((event) => event.type === "output")
    .map((event) => event.data)
    .join("")
  const pid = Number.parseInt(output.trim(), 10)
  assert.ok(
    Number.isInteger(pid) && pid > 1,
    `no pid in ${JSON.stringify(output)}`
  )
  return pid
}

test("interleaves stdout and stderr in the order produced", async (t) => {
  const root = await makeRoot(t)
  const result = await execute(
    root,
    "echo one; sleep 0.05; echo two >&2; sleep 0.05; echo three",
    undefined,
    BASH
  )
  assert.equal(result.output, "one\ntwo\nthree\n")
  assert.equal(result.exitCode, 0)
  assert.equal(result.truncated, false)
})

test("reports a non-zero exit code with the command's stderr", async (t) => {
  const root = await makeRoot(t)
  const result = await execute(root, "echo oops >&2; exit 3", undefined, BASH)
  assert.equal(result.exitCode, 3)
  assert.equal(result.output, "oops\n")
})

test("runs through a shell so pipes and redirects work", async (t) => {
  const root = await makeRoot(t)
  const result = await execute(
    root,
    "printf 'b\\na\\n' | sort > sorted.txt; cat sorted.txt",
    undefined,
    BASH
  )
  assert.equal(result.exitCode, 0)
  assert.equal(result.output, "a\nb\n")
  assert.ok((await stat(path.join(root, "sorted.txt"))).isFile())
})

test("streams output before the command exits", async (t) => {
  const root = await makeRoot(t)
  const seen: { readonly at: number; readonly event: ExecuteEvent }[] = []
  const start = Date.now()
  for await (const event of executeStream(
    root,
    "echo early; sleep 0.4",
    undefined,
    BASH
  )) {
    seen.push({ at: Date.now() - start, event })
  }

  const [first] = seen
  assert.ok(first !== undefined)
  assert.deepEqual(first.event, { type: "output", data: "early\n" })
  const exit = seen.at(-1)
  assert.ok(exit !== undefined)
  assert.equal(exit.event.type, "exit")
  assert.ok(
    exit.at - first.at > 150,
    `output arrived ${exit.at - first.at}ms before exit; expected streaming`
  )
})

test("a timeout keeps the partial output and still terminates the stream", async (t) => {
  const root = await makeRoot(t)
  const events = await collect(
    executeStream(root, "echo partial; sleep 5", { timeoutSeconds: 0.3 }, BASH)
  )

  const exits = events.filter((event) => event.type === "exit")
  assert.equal(exits.length, 1)
  assert.equal(events.at(-1), exits[0])
  assert.deepEqual(exits[0], {
    type: "exit",
    exitCode: 124,
    truncated: false,
  })
  const output = events
    .filter((event) => event.type === "output")
    .map((event) => event.data)
    .join("")
  assert.match(output, /^partial\n/)
  assert.match(output, /timed out after 0\.3 seconds/)
})

test("a timeout kills the whole process group", async (t) => {
  const root = await makeRoot(t)
  const events = await collect(
    executeStream(
      root,
      "sleep 5 & echo $!; wait",
      { timeoutSeconds: 0.3 },
      BASH
    )
  )
  const pid = firstPid(events)
  assert.ok(await waitForDeath(pid), `pid ${pid} outlived the timeout`)
})

test("caps captured output and reports truncation", async (t) => {
  const root = await makeRoot(t)
  const result = await execute(
    root,
    "printf '0123456789'",
    { maxOutputBytes: 4 },
    BASH
  )
  assert.equal(result.truncated, true)
  assert.equal(result.exitCode, 0)
  assert.ok(result.output.startsWith("0123"))
  assert.ok(!result.output.includes("456789"))
})

test("keeps a multi-byte character intact across a chunk boundary", async (t) => {
  const root = await makeRoot(t)
  const events = await collect(
    executeStream(
      root,
      "printf '\\xe2'; sleep 0.1; printf '\\x98\\x83'",
      undefined,
      BASH
    )
  )
  const outputs = events.filter((event) => event.type === "output")
  for (const event of outputs) {
    assert.ok(
      !event.data.includes("�"),
      `broken sequence in ${JSON.stringify(event.data)}`
    )
  }
  assert.equal(outputs.map((event) => event.data).join(""), "☃")
})

test("refuses a cwd outside the root without throwing", async (t) => {
  const root = await makeRoot(t)
  const events = await collect(
    executeStream(root, "pwd", { cwd: path.join(root, "..") }, BASH)
  )
  assert.equal(events.length, 2)
  assert.equal(events[0]?.type, "output")
  assert.match(
    events[0]?.type === "output" ? events[0].data : "",
    /outside the workstation root/
  )
  assert.deepEqual(events[1], {
    type: "exit",
    exitCode: 1,
    truncated: false,
  })
})

test("hides the server's environment but keeps the allowlist", async (t) => {
  const root = await makeRoot(t)
  process.env["WORKSTATION_TEST_SECRET"] = "leaked"
  t.after(() => {
    delete process.env["WORKSTATION_TEST_SECRET"]
  })

  const result = await execute(
    root,
    'echo "secret=[$WORKSTATION_TEST_SECRET]"; echo "path=${PATH:+set}"',
    undefined,
    BASH
  )
  assert.equal(result.exitCode, 0)
  assert.equal(result.output, "secret=[]\npath=set\n")
})

test("lets options.env override the allowlisted environment", async (t) => {
  const root = await makeRoot(t)
  const result = await execute(
    root,
    "echo $HOME",
    { env: { HOME: "/tmp/workstation-home" } },
    BASH
  )
  assert.equal(result.output, "/tmp/workstation-home\n")
})

test("kills the process group when the consumer breaks early", async (t) => {
  const root = await makeRoot(t)
  const events: ExecuteEvent[] = []
  for await (const event of executeStream(
    root,
    "sleep 5 & echo $!; wait",
    undefined,
    BASH
  )) {
    events.push(event)
    break
  }
  const pid = firstPid(events)
  assert.ok(await waitForDeath(pid), `pid ${pid} survived the break`)
})

test("reports a spawn failure as output plus a terminal event", async (t) => {
  const root = await makeRoot(t)
  const events = await collect(
    executeStream(root, "echo hi", undefined, {
      shell: path.join(root, "missing-shell"),
    })
  )
  assert.equal(events.filter((event) => event.type === "exit").length, 1)
  assert.deepEqual(events.at(-1), {
    type: "exit",
    exitCode: 1,
    truncated: false,
  })
  assert.match(
    events[0]?.type === "output" ? events[0].data : "",
    /^Error: file not found$/
  )
})

test("refuses a non-positive timeout instead of killing instantly", async (t) => {
  const root = await makeRoot(t)
  const result = await execute(root, "echo hi", { timeoutSeconds: 0 }, BASH)
  assert.equal(result.exitCode, 1)
  assert.equal(result.truncated, false)
  assert.match(result.output, /timeout must be positive, got 0/)
})

test("refuses a non-positive output cap", async (t) => {
  const root = await makeRoot(t)
  const result = await execute(root, "echo hi", { maxOutputBytes: 0 }, BASH)
  assert.equal(result.exitCode, 1)
  assert.match(result.output, /maxOutputBytes must be positive, got 0/)
})
