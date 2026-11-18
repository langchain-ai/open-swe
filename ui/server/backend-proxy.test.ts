import {
  type IncomingMessage,
  type RequestListener,
  type Server,
  type ServerResponse,
  createServer,
} from "node:http"
import type { AddressInfo } from "node:net"
import { text } from "node:stream/consumers"
import { gzipSync } from "node:zlib"
import { afterEach, describe, expect, it } from "vitest"
import backendProxy from "./backend-proxy"

describe("backendProxy", () => {
  let backend: Server

  async function serveBackend(handler: RequestListener): Promise<void> {
    backend = createServer(handler)
    await new Promise<void>((resolve) =>
      backend.listen(0, "127.0.0.1", resolve)
    )
    const { port } = backend.address() as AddressInfo
    process.env.DASHBOARD_API_URL = `http://127.0.0.1:${port}`
  }

  afterEach(() => {
    backend.closeAllConnections()
    backend.close()
    delete process.env.DASHBOARD_API_URL
  })

  it("forwards the request's end-to-end headers", async () => {
    let received: { req: IncomingMessage; body: string } | undefined
    await serveBackend(async (req, res) => {
      received = { req, body: await text(req) }
      res.end("{}")
    })

    await backendProxy({
      req: new Request(
        "https://dashboard.example.com/dashboard/api/threads/pull-request-checks",
        {
          method: "POST",
          headers: {
            host: "dashboard.example.com",
            "content-length": "2",
            "proxy-authorization": "Basic c2VjcmV0",
            "accept-encoding": "gzip, deflate, br, zstd",
            "content-type": "application/json",
            cookie: "session=abc",
          },
          body: "{}",
        }
      ),
    })

    const headers = received!.req.headers
    expect(headers["proxy-authorization"]).toBeUndefined()
    expect(headers.host).toBe(new URL(process.env.DASHBOARD_API_URL!).host)
    expect(headers["content-length"]).toBe("2")
    expect(headers["transfer-encoding"]).toBeUndefined()
    expect(headers["accept-encoding"]).toBe("gzip, deflate, br, zstd")
    expect(headers["content-type"]).toBe("application/json")
    expect(headers.cookie).toBe("session=abc")
    expect(received!.body).toBe("{}")
  })

  it("drops the length of a body it does not send", async () => {
    let received: IncomingMessage | undefined
    await serveBackend((req, res) => {
      received = req
      res.end("{}")
    })

    const response = await backendProxy({
      req: new Request("https://dashboard.example.com/dashboard/api/me", {
        headers: { "content-length": "40" },
      }),
    })

    expect(response.status).toBe(200)
    expect(received!.headers["content-length"]).toBeUndefined()
  })

  it("chunks a body sent without a length", async () => {
    let received: { req: IncomingMessage; body: string } | undefined
    await serveBackend(async (req, res) => {
      received = { req, body: await text(req) }
      res.end("{}")
    })

    await backendProxy({
      req: new Request(
        "https://dashboard.example.com/dashboard/api/plugins/installation-a",
        { method: "DELETE", body: '{"documents":["a"]}' }
      ),
    })

    expect(received!.req.headers["transfer-encoding"]).toBe("chunked")
    expect(received!.body).toBe('{"documents":["a"]}')
  })

  it("tells the backend which domain the browser used", async () => {
    let origin: string | string[] | undefined
    await serveBackend((req, res) => {
      origin = req.headers["x-open-swe-public-origin"]
      res.end("{}")
    })

    await backendProxy({
      req: new Request("http://robin.example.com/dashboard/api/me", {
        headers: {
          "x-forwarded-proto": "https",
          "x-open-swe-public-origin": "https://attacker.example.com",
        },
      }),
    })

    expect(origin).toBe("https://robin.example.com")
  })

  it("passes the backend's encoded body and cookies through as sent", async () => {
    const gzipped = gzipSync(JSON.stringify({ messages: ["x".repeat(4096)] }))
    await serveBackend((_req, res) => {
      res.writeHead(200, {
        "content-type": "application/json",
        "content-encoding": "gzip",
        "content-length": gzipped.length,
        "set-cookie": ["a=1; Path=/", "b=2; Path=/"],
      })
      res.end(gzipped)
    })

    const response = await backendProxy({
      req: new Request(
        "https://dashboard.example.com/dashboard/api/threads/thread-a/state",
        { headers: { "accept-encoding": "gzip" } }
      ),
    })

    expect(response.headers.get("content-encoding")).toBe("gzip")
    expect(response.headers.get("content-length")).toBe(String(gzipped.length))
    expect(response.headers.getSetCookie()).toEqual([
      "a=1; Path=/",
      "b=2; Path=/",
    ])
    expect(Buffer.from(await response.arrayBuffer())).toEqual(gzipped)
  })

  it("answers a bodiless status with no body and reuses its connection", async () => {
    await serveBackend((_req, res) => {
      res.writeHead(204).end()
    })
    let connections = 0
    backend.on("connection", () => connections++)
    const remove = () =>
      backendProxy({
        req: new Request(
          "https://dashboard.example.com/dashboard/api/plugins/installation-a",
          { method: "DELETE", body: "" }
        ),
      })

    const first = await remove()
    await new Promise((resolve) => setTimeout(resolve, 20))
    const second = await remove()

    expect([first.status, second.status]).toEqual([204, 204])
    expect(first.body).toBeNull()
    expect(connections).toBe(1)
  })

  it("delivers event-stream chunks before the upstream closes", async () => {
    let stream!: ServerResponse
    await serveBackend((_req, res) => {
      res.writeHead(200, { "content-type": "text/event-stream" })
      res.write("data: first\n\n")
      stream = res
    })

    const response = await backendProxy({
      req: new Request(
        "https://dashboard.example.com/dashboard/api/threads/thread-a/stream/events",
        { method: "POST" }
      ),
    })
    const reader = response.body!.getReader()
    try {
      expect(new TextDecoder().decode((await reader.read()).value)).toBe(
        "data: first\n\n"
      )
      stream.write("data: second\n\n")
      expect(new TextDecoder().decode((await reader.read()).value)).toBe(
        "data: second\n\n"
      )
    } finally {
      stream.end()
      await reader.cancel()
    }
  })

  it("closes the backend request when the client leaves before it answers", async () => {
    let arrived!: () => void
    const reached = new Promise<void>((resolve) => (arrived = resolve))
    let closed!: Promise<void>
    await serveBackend((_req, res) => {
      closed = new Promise((resolve) => res.on("close", resolve))
      arrived()
    })
    const client = new AbortController()

    const response = backendProxy({
      req: new Request(
        "https://dashboard.example.com/dashboard/api/threads/thread-a/stream/events",
        { method: "POST", signal: client.signal }
      ),
    })
    await reached
    client.abort()

    expect((await response).status).toBe(499)
    await closed
  })

  it("fails the response when the backend resets the connection", async () => {
    let stream!: ServerResponse
    await serveBackend((_req, res) => {
      res.writeHead(200, { "content-type": "text/event-stream" })
      res.write("data: first\n\n")
      stream = res
    })

    const response = await backendProxy({
      req: new Request(
        "https://dashboard.example.com/dashboard/api/threads/thread-a/stream/events",
        { method: "POST" }
      ),
    })
    stream.socket!.resetAndDestroy()

    await expect(response.text()).rejects.toThrow("aborted")
  })
})
