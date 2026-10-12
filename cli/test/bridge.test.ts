import { describe, expect, test } from "bun:test"
import { mkdtemp } from "node:fs/promises"
import { tmpdir } from "node:os"
import { join } from "node:path"

import { ApiClient } from "../src/api.ts"
import { SessionCredential } from "../src/credentials.ts"
import { Bridge } from "open-swe-bridge-client"
import { isRecord } from "../src/json.ts"

interface Reply {
  requestId: string
  body: Record<string, unknown>
  cookie: string | null
  origin: string | null
  hasAuthorization: boolean
}

interface Fake {
  api: ApiClient
  firstReply: Promise<Reply>
  opened: () => Record<string, unknown>[]
  deleted: () => string[]
  /** The `held` list each long poll sent, in order. */
  held: () => unknown[]
  stop: () => Promise<void>
}

/** A backend that hands out one request and records how the CLI answers it. */
function fakeBackend(
  queue: readonly {
    request_id: string
    method: string
    params: Record<string, unknown>
  }[],
  options: { reopen404?: boolean; replyStatus?: number } = {}
): Fake {
  const opens: Record<string, unknown>[] = []
  const held: unknown[] = []
  const deletes: string[] = []
  const pending = [...queue]
  let settleReply: (reply: Reply) => void = () => {}
  const firstReply = new Promise<Reply>((resolve) => {
    settleReply = resolve
  })

  const server = Bun.serve({
    hostname: "127.0.0.1",
    port: 0,
    async fetch(request) {
      const url = new URL(request.url)
      const path = url.pathname.replace("/dashboard/api", "")
      if (request.method === "POST" && path === "/bridges") {
        const body: unknown = await request.json()
        if (isRecord(body)) opens.push(body)
        if (
          options.reopen404 === true &&
          isRecord(body) &&
          body["bridge_id"] !== null
        ) {
          return new Response(JSON.stringify({ detail: "not found" }), {
            status: 404,
          })
        }
        return Response.json({
          bridge_id: "bridge-1",
          heartbeat_interval_seconds: 3600,
          alive_threshold_seconds: 7200,
        })
      }
      if (request.method === "POST" && path.endsWith("/requests/claim")) {
        const body: unknown = await request.json()
        if (isRecord(body)) held.push(body["held"])
        const requests = pending.splice(0, pending.length)
        return Response.json({ requests })
      }
      if (request.method === "POST" && path.includes("/requests/")) {
        const body: unknown = await request.json()
        settleReply({
          requestId: path.split("/requests/")[1] ?? "",
          body: isRecord(body) ? body : {},
          cookie: request.headers.get("cookie"),
          origin: request.headers.get("origin"),
          hasAuthorization: request.headers.has("authorization"),
        })
        return options.replyStatus === undefined
          ? new Response(null, { status: 204 })
          : Response.json(
              { detail: "sandbox bridge request is already answered" },
              { status: options.replyStatus }
            )
      }
      if (request.method === "DELETE") {
        deletes.push(path)
        return new Response(null, { status: 204 })
      }
      return new Response(null, { status: 404 })
    },
  })

  return {
    api: new ApiClient(
      `http://127.0.0.1:${server.port}`,
      new SessionCredential("jwt-token", "session (test)")
    ),
    firstReply,
    opened: () => opens,
    deleted: () => deletes,
    held: () => held,
    stop: async () => {
      await server.stop(true)
    },
  }
}

describe("Bridge", () => {
  test("serves an execute request and posts the result back", async () => {
    const root = await mkdtemp(join(tmpdir(), "open-swe-bridge-"))
    const fake = fakeBackend([
      {
        request_id: "req-1",
        method: "execute",
        params: { command: "echo bridged", timeout: 30 },
      },
    ])
    const bridge = await Bridge.open(fake.api.bridges(), {
      client: "cli",
      credentialRejected: "rejected",
      log: () => {},
      rootPath: root,
      label: "repo",
      bridgeId: null,
    })
    bridge.start(() => {})
    try {
      const reply = await fake.firstReply
      expect(reply.requestId).toBe("req-1")
      expect(reply.cookie).toBe("osw_session=jwt-token")
      expect(reply.origin).toBe(fake.api.backend)
      expect(reply.hasAuthorization).toBe(false)
      const result = reply.body["result"]
      expect(isRecord(result)).toBe(true)
      if (!isRecord(result)) throw new Error("expected a result object")
      expect(result["exit_code"]).toBe(0)
      expect(result["truncated"]).toBe(false)
      expect(String(result["output"]).trim()).toBe("bridged")
      expect(fake.opened()[0]).toEqual({
        client: "cli",
        root_path: root,
        hostname: expect.any(String),
        label: "repo",
        bridge_id: null,
      })
    } finally {
      await bridge.close()
      await fake.stop()
    }
    expect(fake.deleted()).toEqual(["/bridges/bridge-1"])
  })

  test("stops holding a request the server already settled", async () => {
    const fake = fakeBackend(
      [{ request_id: "req-3", method: "execute", params: { command: "true" } }],
      { replyStatus: 409 }
    )
    const bridge = await Bridge.open(fake.api.bridges(), {
      client: "cli",
      credentialRejected: "rejected",
      log: () => {},
      rootPath: await mkdtemp(join(tmpdir(), "open-swe-bridge-")),
      label: null,
      bridgeId: null,
    })
    bridge.start(() => {})
    try {
      await fake.firstReply
      const deadline = Date.now() + 5_000
      const pollsBefore = fake.held().length
      // The next poll after the 409 must not name req-3, or the id is held for
      // good and the list eventually outgrows what the server accepts.
      while (
        Date.now() < deadline &&
        !fake
          .held()
          .slice(pollsBefore)
          .some((held) => Array.isArray(held) && held.length === 0)
      ) {
        await new Promise((done) => setTimeout(done, 20))
      }
      expect(
        fake
          .held()
          .slice(pollsBefore)
          .some((held) => Array.isArray(held) && held.length === 0)
      ).toBe(true)
    } finally {
      await bridge.close()
      await fake.stop()
    }
  })

  test("never swaps a resumed thread's missing bridge for a new one", async () => {
    const fake = fakeBackend([], { reopen404: true })
    try {
      await expect(
        Bridge.open(fake.api.bridges(), {
          client: "cli",
          credentialRejected: "rejected",
          log: () => {},
          rootPath: await mkdtemp(join(tmpdir(), "open-swe-bridge-")),
          label: null,
          bridgeId: "stale-bridge",
        })
      ).rejects.toMatchObject({ status: 404 })
      expect(fake.opened().map((body) => body["bridge_id"])).toEqual([
        "stale-bridge",
      ])
    } finally {
      await fake.stop()
    }
  })

  test("answers an unsupported method with an error", async () => {
    const fake = fakeBackend([
      { request_id: "req-2", method: "teleport", params: {} },
    ])
    const bridge = await Bridge.open(fake.api.bridges(), {
      client: "cli",
      credentialRejected: "rejected",
      log: () => {},
      rootPath: await mkdtemp(join(tmpdir(), "open-swe-bridge-")),
      label: null,
      bridgeId: null,
    })
    bridge.start(() => {})
    try {
      const reply = await fake.firstReply
      expect(reply.body).toEqual({ error: "unsupported method teleport" })
    } finally {
      await bridge.close()
      await fake.stop()
    }
  })
})
