import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
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
})
