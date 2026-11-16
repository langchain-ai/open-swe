import { afterEach, describe, expect, test } from "bun:test"
import { mkdir, mkdtemp, realpath, rm, writeFile } from "node:fs/promises"
import { tmpdir } from "node:os"
import { join } from "node:path"

import type { TaskActivity, WorkerActivity } from "../src/api.ts"
import { isRecord } from "../src/json.ts"

const MAIN = join(import.meta.dir, "../src/main.ts")

type Message = Record<string, unknown>

interface Scenario {
  /** What each `/workers` poll answers, in turn; the last one repeats. */
  activity?: TaskActivity[]
  /** The thread's messages once it settles, given the prompt the CLI sent. */
  messages?: (prompt: Message) => Message[]
  /** The thread state's pending `interrupt()`s, which pause a run that ended as `success`. */
  interrupts?: unknown[]
  /** Each run's status as MDA reports it, by run id; a run not listed succeeded. */
  runs?: Record<string, string>
  /** Answers a thread route (`GET /thread/state`, …) in place of the scenario, when it returns one. */
  respond?: (route: string) => Response | undefined
}

interface Backend {
  url: string
  /** Every request, as `METHOD /path?query`, without the `/dashboard/api` prefix. */
  calls: string[]
  /** The message the CLI's `run.start` carried. */
  prompt(): Message
  /** Resolves on the first `/workers` poll, once the CLI is waiting on the thread. */
  waiting: Promise<void>
  stop(): Promise<void>
}

function worker(state: string): WorkerActivity {
  return {
    worker_thread_id: "worker-1",
    thread_url: "https://robin.test/agents/worker-1",
    instructions: "Implement the parser\nand its tests",
    state,
    run_status: null,
    active: ["starting", "running", "reporting"].includes(state),
  }
}

function activity(
  settled: boolean,
  workers: WorkerActivity[] = [],
  runs: string[] = []
): TaskActivity {
  return { runs, settled, workers }
}

function fakeBackend(scenario: Scenario = {}): Backend {
  const calls: string[] = []
  const polls = [...(scenario.activity ?? [activity(true)])]
  let prompt: Message | null = null
  let markWaiting = (): void => {}
  const waiting = new Promise<void>((resolve) => {
    markWaiting = resolve
  })
  const server = Bun.serve({
    hostname: "127.0.0.1",
    port: 0,
    async fetch(request) {
      const url = new URL(request.url)
      const path = url.pathname.replace("/dashboard/api", "")
      calls.push(`${request.method} ${path}${url.search}`)
      if (path === "/bridges")
        return Response.json({
          bridge_id: "bridge-1",
          heartbeat_interval_seconds: 3600,
          alive_threshold_seconds: 7200,
        })
      if (path.startsWith("/bridges/")) {
        if (path.endsWith("/requests/claim")) {
          await Bun.sleep(50)
          return Response.json({ requests: [] })
        }
        return new Response(null, { status: 204 })
      }
      // The thread's own routes, by what follows its id.
      const route = `${request.method} ${path.replace(/^\/threads\/[^/]+/, "/thread")}`
      const replaced = scenario.respond?.(route)
      if (replaced !== undefined) return replaced
      if (route === "POST /thread/commands") {
        const body: unknown = await request.json()
        const params = isRecord(body) ? body["params"] : null
        const input = isRecord(params) ? params["input"] : null
        const messages = isRecord(input) ? input["messages"] : null
        const last: unknown = Array.isArray(messages) ? messages.at(-1) : null
        prompt = isRecord(last) ? last : null
        return Response.json({
          id: 1,
          type: "success",
          result: { run_id: "run-1" },
        })
      }
      if (route === "GET /thread/workers") {
        markWaiting()
        return Response.json(polls.length > 1 ? polls.shift() : polls[0])
      }
      if (route === "GET /thread/state")
        return Response.json({
          values: {
            messages:
              prompt === null ? [] : (scenario.messages?.(prompt) ?? [prompt]),
          },
          interrupts: scenario.interrupts ?? [],
          tasks: [],
        })
      const run = /^GET \/thread\/runs\/([^/]+)$/.exec(route)?.[1]
      if (run !== undefined)
        return Response.json({
          run_id: run,
          status: scenario.runs?.[run] ?? "success",
        })
      if (path.endsWith("/cancel")) return Response.json({})
      return Response.json({ detail: "not found" }, { status: 404 })
    },
  })
  return {
    url: `http://127.0.0.1:${server.port}`,
    calls,
    prompt: () => {
      if (prompt === null) throw new Error("the CLI started no run")
      return prompt
    },
    waiting,
    stop: () => server.stop(true),
  }
}

interface Finished {
  stdout: string
  stderr: string
  exitCode: number
}

interface Running {
  process: Bun.Subprocess
  finished: Promise<Finished>
}

const homes: string[] = []
const backends: Backend[] = []

afterEach(async () => {
  await Promise.all(backends.splice(0).map((backend) => backend.stop()))
  await Promise.all(
    homes.splice(0).map((home) => rm(home, { recursive: true, force: true }))
  )
})

function backendFor(scenario: Scenario = {}): Backend {
  const backend = fakeBackend(scenario)
  backends.push(backend)
  return backend
}

interface RunOptions {
  args?: readonly string[]
  /** Threads this machine started before, as `robin run` remembers them. */
  bridged?: readonly string[]
}

/** Starts `robin` in a fresh home, which is also its directory, signed in with a session. */
async function start(
  backend: Backend,
  options: RunOptions = {}
): Promise<Running> {
  const home = await realpath(await mkdtemp(join(tmpdir(), "robin-run-")))
  homes.push(home)
  if (options.bridged !== undefined) {
    const threads = Object.fromEntries(
      options.bridged.map((id) => [id, { bridgeId: "bridge-1", root: home }])
    )
    await mkdir(join(home, ".open-swe"), { recursive: true })
    await writeFile(
      join(home, ".open-swe", "bridges.json"),
      JSON.stringify({ threads })
    )
  }
  const child = Bun.spawn(
    [process.execPath, MAIN, ...(options.args ?? ["run", "is it green?"])],
    {
      cwd: home,
      env: {
        PATH: process.env["PATH"] ?? "",
        HOME: home,
        OPEN_SWE_BACKEND_URL: backend.url,
        OPEN_SWE_SESSION: "jwt-token",
      },
      stdin: "ignore",
      stdout: "pipe",
      stderr: "pipe",
    }
  )
  const finished = Promise.all([
    new Response(child.stdout).text(),
    new Response(child.stderr).text(),
    child.exited,
  ]).then(([stdout, stderr, exitCode]) => ({ stdout, stderr, exitCode }))
  return { process: child, finished }
}

async function robin(
  backend: Backend,
  options: RunOptions = {}
): Promise<Finished> {
  return await (
    await start(backend, options)
  ).finished
}

function answer(args: Record<string, unknown>): Message {
  return {
    type: "ai",
    id: crypto.randomUUID(),
    content: "",
    tool_calls: [
      { name: "cli_result", args, id: crypto.randomUUID(), type: "tool_call" },
    ],
  }
}

function human(content: string): Message {
  return { type: "human", id: crypto.randomUUID(), content }
}

describe("robin run", () => {
  test("prints the agent's answer and exits with its code, reading no event stream", async () => {
    const backend = backendFor({
      messages: (prompt) => [
        prompt,
        answer({ stdout: "all green", exit_code: 3 }),
      ],
    })

    const run = await robin(backend)

    expect(run).toMatchObject({ stdout: "all green\n", exitCode: 3 })
    expect(backend.prompt()["id"]).toEqual(expect.any(String))
    expect(backend.calls).toContainEqual(
      expect.stringMatching(/^GET \/threads\/[^/]+\/workers\?after=run-1$/)
    )
    expect(
      backend.calls.filter((call) => call.includes("/stream/events"))
    ).toEqual([])
  })

  test("waits for its workers and ends on the coordinator's last answer", async () => {
    const backend = backendFor({
      activity: [
        activity(false, [worker("running")]),
        activity(true, [worker("done")]),
      ],
      messages: (prompt) => [
        prompt,
        answer({ stdout: "waiting on a worker", exit_code: 0 }),
        human("worker-1 finished"),
        answer({ stdout: "worker done; tests fail", exit_code: 1 }),
        human("worker-1 has nothing more"),
        answer({ stdout: "", exit_code: 0, unchanged: true }),
      ],
    })

    const run = await robin(backend)

    expect(run).toMatchObject({
      stdout: "worker done; tests fail\n",
      exitCode: 1,
    })
    expect(run.stderr).toContain(
      "Worker running: https://robin.test/agents/worker-1 — Implement the parser\n"
    )
    expect(run.stderr).toContain("Waiting for 1 worker to report\n")
    expect(run.stderr).toContain(
      "Worker done: https://robin.test/agents/worker-1\n"
    )
  })

  test("never takes an earlier command's answer on a continued thread", async () => {
    const backend = backendFor({
      messages: (prompt) => [
        human("is it green?"),
        answer({ stdout: "all green", exit_code: 0 }),
        prompt,
        { type: "ai", id: crypto.randomUUID(), content: "Done." },
      ],
    })

    const run = await robin(backend, {
      args: ["run", "--thread", "thread-1", "and now?"],
      bridged: ["thread-1"],
    })

    expect(run).toMatchObject({ stdout: "", exitCode: 1 })
    expect(run.stderr).toContain(
      "robin: the agent finished without calling cli_result\n"
    )
    expect(backend.calls).toContain("POST /threads/thread-1/commands")
  })

  test.each([
    ["error", "failed"],
    ["timeout", "failed"],
    ["interrupted", "interrupted"],
  ])(
    "reports the coordinator's last run ending %s with the thread's link",
    async (status, reported) => {
      const backend = backendFor({
        // A worker's report woke the coordinator in a run of its own, and that run ended badly.
        activity: [activity(true, [worker("done")], ["run-2"])],
        runs: { "run-2": status },
        messages: (prompt) => [
          prompt,
          answer({ stdout: "waiting on a worker", exit_code: 0 }),
        ],
      })

      const run = await robin(backend)

      expect(run).toMatchObject({ stdout: "", exitCode: 1 })
      expect(run.stderr).toMatch(
        new RegExp(
          `robin: run ${reported}; see ${backend.url}/agents/[0-9a-f-]{36}\\n`
        )
      )
    }
  )

  test("reports a run paused on a question as interrupted", async () => {
    const backend = backendFor({
      interrupts: [{ value: "Which branch?", id: "interrupt-1" }],
      messages: (prompt) => [prompt],
    })

    const run = await robin(backend)

    expect(run).toMatchObject({ stdout: "", exitCode: 1 })
    expect(run.stderr).toContain("robin: run interrupted; see ")
  })

  test("Ctrl-C cancels the workers and the thread, then exits 130", async () => {
    const backend = backendFor({
      activity: [activity(false, [worker("running")])],
    })
    const running = await start(backend)
    await backend.waiting

    running.process.kill("SIGINT")
    const run = await running.finished

    expect(run).toMatchObject({ stdout: "", exitCode: 130 })
    const cancels = backend.calls.filter((call) => call.endsWith("/cancel"))
    expect(cancels).toEqual([
      expect.stringMatching(/^POST \/threads\/[^/]+\/workers\/cancel$/),
      expect.stringMatching(/^POST \/threads\/[^/]+\/cancel$/),
    ])
  })

  test("retries the backend through a restart", async () => {
    let restarting = true
    const backend = backendFor({
      messages: (prompt) => [prompt, answer({ stdout: "ok", exit_code: 0 })],
      respond: (route) => {
        if (route !== "GET /thread/state" || !restarting) return undefined
        restarting = false
        return Response.json({ detail: "restarting" }, { status: 503 })
      },
    })

    const run = await robin(backend)

    expect(run).toMatchObject({ stdout: "ok\n", exitCode: 0 })
    expect(run.stderr).toContain(
      "Lost the connection to the backend (HTTP 503: restarting); retrying, attempt 1\n"
    )
  })

  test("tells a person whose session the backend rejects to sign in again", async () => {
    const backend = backendFor({
      respond: (route) =>
        route === "GET /thread/workers"
          ? Response.json({ detail: "not authenticated" }, { status: 401 })
          : undefined,
    })

    const run = await robin(backend)

    expect(run).toMatchObject({ stdout: "", exitCode: 1 })
    expect(run.stderr).toContain(
      "robin: your session expired — run `robin login` again\n"
    )
  })

  test("fails when the thread's state has lost the prompt, not on an older answer", async () => {
    const backend = backendFor({
      messages: () => [
        human("is it green?"),
        answer({ stdout: "all green", exit_code: 0 }),
      ],
    })

    const run = await robin(backend)

    expect(run).toMatchObject({ stdout: "", exitCode: 1 })
    expect(run.stderr).toContain(
      `robin: the thread's state has no message ${String(backend.prompt()["id"])}, the one this command sent\n`
    )
  })
})
