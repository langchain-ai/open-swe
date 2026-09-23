import { describe, expect, test } from "bun:test"
import { mkdtemp } from "node:fs/promises"
import { tmpdir } from "node:os"
import { join } from "node:path"

import { ApiClient } from "../src/api.ts"
import { Bridge } from "../src/bridge.ts"
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
  stop: () => Promise<void>
}

/** A backend that hands out one request and records how the CLI answers it. */
function fakeBackend(
  queue: readonly {
    request_id: string
    method: string
    params: Record<string, unknown>
  }[],
  options: { reopen404?: boolean } = {}
): Fake {
  const opens: Record<string, unknown>[] = []
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
      if (request.method === "GET" && path.endsWith("/requests")) {
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
        return new Response(null, { status: 204 })
      }
      if (request.method === "DELETE") {
        deletes.push(path)
        return new Response(null, { status: 204 })
      }
      return new Response(null, { status: 404 })
    },
  })

  return {
    api: new ApiClient(`http://127.0.0.1:${server.port}`, "jwt-token"),
    firstReply,
    opened: () => opens,
    deleted: () => deletes,
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
    const bridge = await Bridge.open(fake.api, {
      rootPath: root,
      label: "repo",
      rememberedBridgeId: null,
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

  test("creates a fresh bridge when a remembered id is gone", async () => {
    const fake = fakeBackend([], { reopen404: true })
    const bridge = await Bridge.open(fake.api, {
      rootPath: await mkdtemp(join(tmpdir(), "open-swe-bridge-")),
      label: null,
      rememberedBridgeId: "stale-bridge",
    })
    try {
      expect(bridge.reopened).toBe(false)
      expect(bridge.session.bridgeId).toBe("bridge-1")
      expect(fake.opened().map((body) => body["bridge_id"])).toEqual([
        "stale-bridge",
        null,
      ])
    } finally {
      await fake.stop()
    }
  })

  test("answers an unsupported method with an error", async () => {
    const fake = fakeBackend([
      { request_id: "req-2", method: "teleport", params: {} },
    ])
    const bridge = await Bridge.open(fake.api, {
      rootPath: await mkdtemp(join(tmpdir(), "open-swe-bridge-")),
      label: null,
      rememberedBridgeId: null,
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
