import { describe, expect, test } from "bun:test"

import { ApiClient, ApiError, type CoordinatorThread } from "../src/api.ts"
import { CredentialError, SessionCredential } from "../src/credentials.ts"
import { followTask } from "../src/follow.ts"

/** A thread whose activity reads all fail with `cause`; everything else is never reached. */
function failingThread(cause: () => Error): {
  thread: CoordinatorThread
  reads: () => number
} {
  let reads = 0
  const unreachable = async (): Promise<never> => {
    throw new Error("unreachable")
  }
  return {
    thread: {
      activity: async () => {
        reads += 1
        throw cause()
      },
      waiting: unreachable,
      runStatus: unreachable,
      stop: async () => undefined,
    },
    reads: () => reads,
  }
}

describe("followTask through backend failures", () => {
  test("backs off and gives up after ten retries in a row", async () => {
    const { thread, reads } = failingThread(
      () => new ApiError(503, "restarting")
    )
    const waits: number[] = []

    const following = followTask(thread, "run-1", {
      sleep: async (ms) => {
        waits.push(ms)
      },
    })

    await expect(following).rejects.toBeInstanceOf(ApiError)
    expect(reads()).toBe(11)
    expect(waits).toEqual([
      1_000, 2_000, 4_000, 8_000, 16_000, 30_000, 30_000, 30_000, 30_000,
      30_000,
    ])
  })

  test("retries a dropped connection and a rate limit", async () => {
    let reads = 0
    const failures = [new TypeError("fetch failed"), new ApiError(429, "slow")]
    const outcome = await followTask(
      {
        activity: async () => {
          reads += 1
          const failure = failures.shift()
          if (failure !== undefined) throw failure
          return { runs: [], settled: true, workers: [] }
        },
        waiting: async () => false,
        runStatus: async () => "success",
        stop: async () => undefined,
      },
      "run-1",
      { sleep: async () => undefined }
    )

    expect(outcome).toBe("completed")
    expect(reads).toBe(3)
  })

  test.each([
    [
      "a credential the CLI could not obtain",
      () => new CredentialError("no OIDC token"),
    ],
    ["a rejected credential", () => new ApiError(401, "unauthorized")],
    [
      "a thread the backend cannot find",
      () => new ApiError(404, "thread not found"),
    ],
  ])("does not retry %s", async (_, cause) => {
    const { thread, reads } = failingThread(cause)

    const following = followTask(thread, "run-1", {
      sleep: async () => undefined,
    })

    await expect(following).rejects.toThrow()
    expect(reads()).toBe(1)
  })
})

describe("stopping a coordinator", () => {
  test("cancels its workers, and its runs even when that fails", async () => {
    const posted: string[] = []
    const server = Bun.serve({
      hostname: "127.0.0.1",
      port: 0,
      fetch(request) {
        const path = new URL(request.url).pathname
        posted.push(path.replace("/dashboard/api", ""))
        return path.endsWith("/workers/cancel")
          ? new Response("down", { status: 503 })
          : Response.json({})
      },
    })
    try {
      const api = new ApiClient(
        `http://127.0.0.1:${server.port}`,
        new SessionCredential("jwt-token", "session (test)")
      )
      const thread = api.coordinator("thread-1")
      await expect(thread.stop()).rejects.toBeInstanceOf(ApiError)
      expect(posted).toEqual([
        "/threads/thread-1/workers/cancel",
        "/threads/thread-1/cancel",
      ])
    } finally {
      await server.stop(true)
    }
  })
})
