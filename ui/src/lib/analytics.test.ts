/** @vitest-environment jsdom */

import { afterEach, describe, expect, it, vi } from "vitest"
import type { Analytics } from "@segment/analytics-next"

import { ProductAnalytics } from "./analytics"
import type { SessionUser } from "./api"

vi.mock("./posthog", () => ({
  posthogAnalytics: {
    initialize: vi.fn(async () => {}),
    identify: vi.fn(),
    reset: vi.fn(),
  },
}))

const alice: SessionUser = {
  login: "alice",
  user_id: "alice-uuid",
  email: "alice@example.com",
  github_login: "alice",
  avatar_url: null,
  is_admin: false,
}

class SegmentDouble {
  id: string | null = null
  identify = vi.fn(async (id: string) => {
    this.id = id
  })
  page = vi.fn(async () => {})
  track = vi.fn(async () => {})
  reset = vi.fn(() => {
    this.id = null
  })
  register = vi.fn(async () => {})
  user() {
    return { id: () => this.id }
  }
  async load() {
    return this as unknown as Analytics
  }
}

class DeferredClient {
  client = new SegmentDouble()
  resolve!: (client: Analytics) => void
  promise = new Promise<Analytics>((resolve) => {
    this.resolve = resolve
  })
  async load() {
    return this.promise
  }
}

class SegmentNetwork {
  requests: unknown[] = []
  fetch = vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
    if (url.toString().endsWith("/analytics/config"))
      return Response.json({
        environment: "staging",
        segment_write_key: "browser-source",
      })
    this.requests.push(JSON.parse(String(init?.body)))
    return Response.json({ success: true })
  })
}

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
  localStorage.clear()
})

describe("product analytics", () => {
  it("does not load Segment for an unconfigured deployment", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => Response.json({ environment: "production" }))
    )
    const analytics = new ProductAnalytics()
    const client = new SegmentDouble()
    const load = vi.spyOn(client, "load")
    analytics.page("/agents", "/agents")
    await analytics.initialize(() => client.load())
    analytics.identify(alice)
    analytics.track("agents_message_submitted", {
      mode: "new_turn",
      has_attachments: false,
    })
    expect(load).not.toHaveBeenCalled()
    expect(client.track).not.toHaveBeenCalled()
  })

  it("keeps original routes and times while loading, without duplicate page views", async () => {
    vi.stubGlobal("fetch", new SegmentNetwork().fetch)
    const analytics = new ProductAnalytics()
    const deferred = new DeferredClient()
    analytics.identify(alice)
    analytics.page("/agents/private-thread?secret=value", "/agents/$threadId")
    analytics.page("/agents/private-thread?secret=value", "/agents/$threadId")
    vi.useFakeTimers()
    const started = new Date("2026-11-04T12:00:00Z")
    vi.setSystemTime(started)
    analytics.track("agents_thread_created", {
      run_target: "cloud",
      has_attachments: true,
    })
    const initializing = analytics.initialize(() => deferred.load())
    analytics.page("/usage", "/usage")
    deferred.resolve(await deferred.client.load())
    await initializing
    expect(deferred.client.page.mock.calls).toHaveLength(2)
    expect(deferred.client.page).toHaveBeenNthCalledWith(
      1,
      "/agents/:id",
      expect.objectContaining({ product: "open-swe" }),
      expect.objectContaining({ context: { page: { path: "/agents/:id" } } })
    )
    expect(deferred.client.track).toHaveBeenCalledWith(
      "agents_thread_created",
      expect.objectContaining({ run_target: "cloud", has_attachments: true }),
      expect.objectContaining({
        timestamp: expect.any(Date),
        context: { page: { path: "/agents/:id" } },
      })
    )
    expect(deferred.client.track).toHaveBeenCalledWith(
      expect.any(String),
      expect.any(Object),
      expect.objectContaining({ timestamp: started })
    )
  })

  it("drops pending events on logout and account switches while the client loads", async () => {
    vi.stubGlobal("fetch", new SegmentNetwork().fetch)
    const analytics = new ProductAnalytics()
    const deferred = new DeferredClient()
    analytics.identify(alice)
    analytics.track("agents_message_submitted", {
      mode: "steer",
      has_attachments: false,
    })
    const initializing = analytics.initialize(() => deferred.load())
    analytics.reset()
    analytics.identify({
      ...alice,
      user_id: "bob-uuid",
      email: "bob@example.com",
    })
    analytics.track("agents_message_submitted", {
      mode: "queue",
      has_attachments: true,
    })
    analytics.track(
      "agents_thread_created",
      {
        run_target: "cloud",
        has_attachments: false,
      },
      alice.user_id
    )
    deferred.resolve(await deferred.client.load())
    await initializing
    expect(deferred.client.identify).toHaveBeenLastCalledWith(
      "bob-uuid",
      expect.any(Object),
      expect.any(Object)
    )
    expect(deferred.client.track).toHaveBeenCalledExactlyOnceWith(
      "agents_message_submitted",
      expect.objectContaining({ mode: "queue" }),
      expect.any(Object)
    )
    analytics.identify({ ...alice, user_id: null })
    expect(deferred.client.id).toBeNull()
  })

  it("absorbs SDK load failures and synchronous or asynchronous delivery failures", async () => {
    vi.stubGlobal("fetch", new SegmentNetwork().fetch)
    vi.spyOn(console, "warn").mockImplementation(() => {})
    const unavailable = new ProductAnalytics()
    await expect(
      unavailable.initialize(async () => {
        throw new Error("blocked")
      })
    ).resolves.toBeUndefined()
    const analytics = new ProductAnalytics()
    const client = new SegmentDouble()
    await analytics.initialize(() => client.load())
    analytics.identify(alice)
    client.track.mockImplementationOnce(() => {
      throw new Error("blocked")
    })
    expect(() =>
      analytics.track("agents_message_submitted", {
        mode: "new_turn",
        has_attachments: false,
      })
    ).not.toThrow()
    client.track.mockRejectedValueOnce(new Error("offline"))
    analytics.track("agents_message_submitted", {
      mode: "steer",
      has_attachments: false,
    })
    await Promise.resolve()
    expect(console.warn).toHaveBeenCalled()
  })

  it("scrubs actual SDK envelopes before network delivery, including identify context", async () => {
    const network = new SegmentNetwork()
    vi.stubGlobal("fetch", network.fetch)
    window.history.replaceState(
      {},
      "",
      "/agents/private-thread?token=secret&utm_campaign=private-campaign#private-hash"
    )
    document.title = "Customer secret task"
    const analytics = new ProductAnalytics()
    analytics.identify(alice)
    analytics.page(window.location.pathname, "/agents/$threadId")
    await analytics.initialize()
    vi.useFakeTimers()
    analytics.track("agents_message_submitted", {
      mode: "new_turn",
      has_attachments: false,
    })
    await vi.advanceTimersByTimeAsync(6000)
    const serialized = JSON.stringify(network.requests)
    expect(network.requests).toHaveLength(1)
    for (const forbidden of [
      "private-thread",
      "token=",
      "private-campaign",
      "private-hash",
      "Customer secret task",
      "referrer",
      "userAgent",
      "search",
    ])
      expect(serialized).not.toContain(forbidden)
    expect(serialized).toContain('"path":"/agents/:id"')
    expect(serialized).toContain('"ip":"0.0.0.0"')
    expect(serialized).toContain('"product":"open-swe"')
    expect(serialized).toContain('"type":"page"')
    expect(serialized).toContain('"type":"identify"')
    expect(serialized).toContain('"type":"track"')
    expect(serialized).not.toContain('"All":false')
    expect(localStorage.getItem("ajs_user_id")).toContain("alice-uuid")
    analytics.reset()
    expect(localStorage.getItem("ajs_user_id")).toBeNull()
  })
})
