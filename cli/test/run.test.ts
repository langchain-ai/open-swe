import { afterEach, describe, expect, test } from "bun:test"
import { mkdtemp, realpath, rm } from "node:fs/promises"
import { tmpdir } from "node:os"
import { join } from "node:path"

import type { TaskActivity, WorkerActivity } from "../src/api.ts"
import { isRecord } from "../src/json.ts"

const MAIN = join(import.meta.dir, "../src/main.ts")

interface Scenario {
  /** What each `/workers` poll answers, in turn; the last one repeats. */
  activity?: TaskActivity[]
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
  /** The configurable the CLI's `run.start` carried. */
  configurable(): Record<string, unknown>
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
  let configurable: Record<string, unknown> | null = null
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
      // The thread's own routes, by what follows its id.
      const route = `${request.method} ${path.replace(/^\/threads\/[^/]+/, "/thread")}`
      const replaced = scenario.respond?.(route)
      if (replaced !== undefined) return replaced
      if (route === "POST /thread/commands") {
        const body: unknown = await request.json()
        const params = isRecord(body) ? body["params"] : null
        const config = isRecord(params) ? params["config"] : null
        const sent = isRecord(config) ? config["configurable"] : null
        configurable = isRecord(sent) ? sent : null
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
          values: { messages: [] },
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
    configurable: () => {
      if (configurable === null) throw new Error("the CLI started no run")
      return configurable
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
}

/** Starts `robin` in a fresh home, which is also its directory, signed in with a session. */
async function start(
  backend: Backend,
  options: RunOptions = {}
): Promise<Running> {
  const home = await realpath(await mkdtemp(join(tmpdir(), "robin-run-")))
  homes.push(home)
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

const THREAD_URL = /^http:\/\/127\.0\.0\.1:\d+\/agents\/[0-9a-f-]{36}\n$/

describe("robin run", () => {
  test("prints the thread's URL and exits 0 once its run completes, reading no event stream", async () => {
    const backend = backendFor()

    const run = await robin(backend)

    expect(run.exitCode).toBe(0)
    expect(run.stdout).toMatch(THREAD_URL)
    expect(backend.configurable()).toEqual({
      thread_type: "private",
      repo_explicitly_none: true,
    })
    expect(backend.calls).toContainEqual(
      expect.stringMatching(/^GET \/threads\/[^/]+\/workers\?after=run-1$/)
    )
    expect(
      backend.calls.filter((call) => call.includes("/stream/events"))
    ).toEqual([])
  })

  test("waits for its workers and notes each one as it changes", async () => {
    const backend = backendFor({
      activity: [
        activity(false, [worker("running")]),
        activity(true, [worker("done")]),
      ],
    })

    const run = await robin(backend)

    expect(run.exitCode).toBe(0)
    expect(run.stderr).toContain(
      "Worker running: https://robin.test/agents/worker-1 — Implement the parser\n"
    )
    expect(run.stderr).toContain("Waiting for 1 worker to report\n")
    expect(run.stderr).toContain(
      "Worker done: https://robin.test/agents/worker-1\n"
    )
  })

  test("continues an existing thread without naming its repository again", async () => {
    const backend = backendFor()

    const run = await robin(backend, {
      args: ["run", "--thread", "thread-1", "and now?"],
    })

    expect(run).toMatchObject({
      stdout: `${backend.url}/agents/thread-1\n`,
      exitCode: 0,
    })
    expect(backend.configurable()).toEqual({ thread_type: "private" })
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
      })

      const run = await robin(backend)

      expect(run.exitCode).toBe(1)
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
    })

    const run = await robin(backend)

    expect(run.exitCode).toBe(1)
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

    expect(run.exitCode).toBe(130)
    const cancels = backend.calls.filter((call) => call.endsWith("/cancel"))
    expect(cancels).toEqual([
      expect.stringMatching(/^POST \/threads\/[^/]+\/workers\/cancel$/),
      expect.stringMatching(/^POST \/threads\/[^/]+\/cancel$/),
    ])
  })

  test("retries the backend through a restart", async () => {
    let restarting = true
    const backend = backendFor({
      respond: (route) => {
        if (route !== "GET /thread/state" || !restarting) return undefined
        restarting = false
        return Response.json({ detail: "restarting" }, { status: 503 })
      },
    })

    const run = await robin(backend)

    expect(run.exitCode).toBe(0)
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

    expect(run.exitCode).toBe(1)
    expect(run.stderr).toContain(
      "robin: your session expired — run `robin login` again\n"
    )
  })
})
