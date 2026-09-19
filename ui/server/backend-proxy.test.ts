import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { gunzipSync } from "node:zlib"
import backendProxy from "./backend-proxy"

describe("backendProxy", () => {
  const originalFetch = globalThis.fetch

  beforeEach(() => {
    process.env.DASHBOARD_API_URL = "https://backend.example.com"
  })

  afterEach(() => {
    globalThis.fetch = originalFetch
    delete process.env.DASHBOARD_API_URL
  })

  it("drops request framing headers undici refuses to send", async () => {
    const fetchMock = vi.fn(async () => new Response("{}", { status: 200 }))
    globalThis.fetch = fetchMock as unknown as typeof fetch

    await backendProxy({
      req: new Request(
        "https://dashboard.example.com/dashboard/api/threads/pull-request-checks",
        {
          method: "POST",
          headers: {
            "transfer-encoding": "chunked",
            "content-length": "2",
            connection: "keep-alive",
            "content-type": "application/json",
            cookie: "session=abc",
          },
          body: "{}",
        }
      ),
    })

    const headers = (
      fetchMock.mock.calls[0] as unknown as [string, RequestInit]
    )[1].headers as Headers
    expect(headers.get("transfer-encoding")).toBeNull()
    expect(headers.get("connection")).toBeNull()
    expect(headers.get("content-length")).toBe("2")
    expect(headers.get("content-type")).toBe("application/json")
    expect(headers.get("cookie")).toBe("session=abc")
  })

  const transcript = JSON.stringify({
    messages: [{ content: "hello ".repeat(1000) }],
  })

  it.each([
    ["GET", "state"],
    ["POST", "history"],
  ])(
    "compresses %s %s JSON after upstream decoding",
    async (method, endpoint) => {
      globalThis.fetch = vi.fn(
        async () =>
          new Response(transcript, {
            headers: {
              "content-type": "application/json; charset=utf-8",
              "content-encoding": "br",
              "content-length": "100",
              vary: "Origin",
              etag: '"checkpoint"',
              "set-cookie": "session=renewed; HttpOnly; Secure",
            },
          })
      ) as typeof fetch

      const response = await backendProxy({
        req: new Request(
          `https://dashboard.example.com/dashboard/api/threads/thread-a/${endpoint}`,
          { method, headers: { "accept-encoding": "gzip, deflate, br" } }
        ),
      })
      const compressed = Buffer.from(await response.arrayBuffer())

      expect(response.status).toBe(200)
      expect(response.headers.get("content-encoding")).toBe("gzip")
      expect(response.headers.get("content-length")).toBeNull()
      expect(response.headers.get("vary")).toBe("Origin, Accept-Encoding")
      expect(response.headers.get("etag")).toBe('W/"checkpoint"')
      expect(response.headers.getSetCookie()).toEqual([
        "session=renewed; HttpOnly; Secure",
      ])
      expect(gunzipSync(compressed).toString()).toBe(transcript)
      expect(compressed.byteLength).toBeLessThan(Buffer.byteLength(transcript))
    }
  )

  it.each([
    ["", false],
    ["br", false],
    ["gzip;q=0", false],
    ["gzip;q=0, *;q=1", false],
    ["gzip;q=0.5, br;q=1", true],
    ["*;q=0.5", true],
    ["GZIP; q=1", true],
    ["gzip;q=invalid", false],
  ])("negotiates gzip for Accept-Encoding %s", async (encoding, compressed) => {
    globalThis.fetch = vi.fn(
      async () =>
        new Response(transcript, {
          headers: { "content-type": "application/json" },
        })
    ) as typeof fetch
    const response = await backendProxy({
      req: new Request(
        "https://dashboard.example.com/dashboard/api/threads/thread-a/state",
        { headers: { "accept-encoding": encoding } }
      ),
    })

    expect(response.headers.get("vary")).toBe("Accept-Encoding")
    expect(response.headers.get("content-encoding")).toBe(
      compressed ? "gzip" : null
    )
    const bytes = Buffer.from(await response.arrayBuffer())
    expect(compressed ? gunzipSync(bytes).toString() : bytes.toString()).toBe(
      transcript
    )
  })

  it.each(["request", "response"])(
    "honors %s Cache-Control no-transform",
    async (source) => {
      globalThis.fetch = vi.fn(
        async () =>
          new Response(transcript, {
            headers: {
              "content-type": "application/json",
              ...(source === "response"
                ? { "cache-control": "private, no-transform" }
                : {}),
            },
          })
      ) as typeof fetch
      const response = await backendProxy({
        req: new Request(
          "https://dashboard.example.com/dashboard/api/threads/thread-a/state",
          {
            headers: {
              "accept-encoding": "gzip",
              ...(source === "request"
                ? { "cache-control": "no-transform" }
                : {}),
            },
          }
        ),
      })

      expect(response.headers.get("content-encoding")).toBeNull()
      expect(await response.text()).toBe(transcript)
    }
  )

  it("keeps unrelated JSON responses unchanged", async () => {
    globalThis.fetch = vi.fn(async () =>
      Response.json({ user: "alice" })
    ) as typeof fetch
    const response = await backendProxy({
      req: new Request("https://dashboard.example.com/dashboard/api/profile", {
        headers: { "accept-encoding": "gzip" },
      }),
    })

    expect(response.headers.get("content-encoding")).toBeNull()
    expect(await response.json()).toEqual({ user: "alice" })
  })

  it("delivers event-stream chunks before the upstream closes", async () => {
    let controller!: ReadableStreamDefaultController<Uint8Array>
    const body = new ReadableStream<Uint8Array>({
      start(value) {
        controller = value
      },
    })
    globalThis.fetch = vi.fn(
      async () =>
        new Response(body, {
          headers: { "content-type": "text/event-stream" },
        })
    ) as typeof fetch
    const response = await backendProxy({
      req: new Request(
        "https://dashboard.example.com/dashboard/api/threads/thread-a/stream/events",
        { method: "POST", headers: { "accept-encoding": "gzip" } }
      ),
    })
    const reader = response.body!.getReader()
    try {
      controller.enqueue(new TextEncoder().encode("data: first\n\n"))
      expect(response.headers.get("content-encoding")).toBeNull()
      expect(new TextDecoder().decode((await reader.read()).value)).toBe(
        "data: first\n\n"
      )
      controller.enqueue(new TextEncoder().encode("data: second\n\n"))
      expect(new TextDecoder().decode((await reader.read()).value)).toBe(
        "data: second\n\n"
      )
    } finally {
      controller.close()
      await reader.cancel()
    }
  })
})
