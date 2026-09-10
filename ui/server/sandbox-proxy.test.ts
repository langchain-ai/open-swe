import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import sandboxProxy from "./sandbox-proxy"

type Call = [string, RequestInit]

const MINT = {
  service_url: "https://svc.sandbox.example",
  token: "service-token",
  expires_at: new Date(Date.now() + 3_600_000).toISOString(),
}

function mockFetch(upstream: () => Response) {
  const fetchMock = vi.fn(async (url: string) =>
    url.includes("/service-url")
      ? new Response(JSON.stringify(MINT), {
          headers: { "content-type": "application/json" },
        })
      : upstream()
  )
  globalThis.fetch = fetchMock as unknown as typeof fetch
  return fetchMock
}

function request(path: string, init?: RequestInit) {
  return { req: new Request(`https://dashboard.example.com${path}`, init) }
}

describe("sandboxProxy", () => {
  const originalFetch = globalThis.fetch

  beforeEach(() => {
    process.env.DASHBOARD_API_URL = "https://backend.example.com"
  })

  afterEach(() => {
    globalThis.fetch = originalFetch
    delete process.env.DASHBOARD_API_URL
  })

  it("strips the proxy prefix and attaches the service token", async () => {
    const fetchMock = mockFetch(() => new Response("ok"))

    const response = await sandboxProxy(
      request("/sandbox/thread-a/3000/assets/app.js?v=1", {
        headers: { cookie: "osw_session=abc; app_pref=dark" },
      })
    )

    expect(response.status).toBe(200)
    const calls = fetchMock.mock.calls as unknown as Call[]
    const mint = calls[0]!
    const upstream = calls[1]!
    expect(mint[0]).toBe(
      "https://backend.example.com/dashboard/api/threads/thread-a/service-url?port=3000"
    )
    expect(upstream[0]).toBe("https://svc.sandbox.example/assets/app.js?v=1")
    const headers = upstream[1].headers as Headers
    expect(headers.get("x-langsmith-sandbox-service-token")).toBe(
      "service-token"
    )
    // The dashboard session authorizes the proxy; the sandbox never sees it.
    expect(headers.get("cookie")).toBe("app_pref=dark")
  })

  it("mints per session, not per request", async () => {
    const fetchMock = mockFetch(() => new Response("ok"))
    const withCookie = (cookie: string) =>
      sandboxProxy(request("/sandbox/thread-b/3000/", { headers: { cookie } }))

    await withCookie("osw_session=first")
    // A cookie the service set for itself comes back on the next request; only
    // the session decides whether the minted token may be reused.
    await withCookie("osw_session=first; sid=1")
    await withCookie("osw_session=second")

    const mints = (fetchMock.mock.calls as unknown as Call[]).filter(([url]) =>
      url.includes("/service-url")
    )
    expect(mints).toHaveLength(2)
  })

  it("keeps a redirect inside the proxy prefix", async () => {
    mockFetch(
      () => new Response(null, { status: 302, headers: { location: "/login" } })
    )

    const response = await sandboxProxy(request("/sandbox/thread-c/3000/app"))

    expect(response.status).toBe(302)
    expect(response.headers.get("location")).toBe(
      "/sandbox/thread-c/3000/login"
    )
  })

  it("scopes the service's cookies to the proxy prefix", async () => {
    mockFetch(
      () =>
        new Response("ok", {
          headers: { "set-cookie": "sid=1; Path=/; Domain=svc.example" },
        })
    )

    const response = await sandboxProxy(request("/sandbox/thread-d/3000/"))

    expect(response.headers.getSetCookie()).toEqual([
      "sid=1; Path=/sandbox/thread-d/3000/",
    ])
  })

  it("contains sandbox content in an opaque origin", async () => {
    mockFetch(
      () =>
        new Response("<script>fetch('/dashboard/api/threads')</script>", {
          headers: {
            "content-type": "text/html",
            // The service must not be able to relax its own containment.
            "content-security-policy":
              "sandbox allow-same-origin allow-scripts",
            "content-security-policy-report-only": "default-src *",
          },
        })
    )

    const response = await sandboxProxy(request("/sandbox/thread-g/3000/"))

    expect(response.headers.get("content-security-policy")).toBe(
      "sandbox allow-scripts allow-forms allow-popups allow-popups-to-escape-sandbox allow-downloads"
    )
    expect(
      response.headers.get("content-security-policy-report-only")
    ).toBeNull()
  })

  it("redirects to a trailing slash so relative URLs resolve", async () => {
    const fetchMock = mockFetch(() => new Response("ok"))

    const response = await sandboxProxy(request("/sandbox/thread-e/3000"))

    expect(response.status).toBe(308)
    expect(response.headers.get("location")).toBe("/sandbox/thread-e/3000/")
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it("passes the backend's refusal through", async () => {
    globalThis.fetch = vi.fn(
      async () => new Response("no", { status: 403 })
    ) as unknown as typeof fetch

    const response = await sandboxProxy(request("/sandbox/thread-f/3000/"))

    expect(response.status).toBe(403)
  })
})
