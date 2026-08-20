/** @vitest-environment jsdom */

import { afterEach, describe, expect, it, vi } from "vitest"

import { agentsApi } from "./api"
import { apiWarmupScript } from "./apiWarmup"
import { SIDEBAR_ACTIVE_LIMIT, SIDEBAR_RESOLVED_LIMIT } from "./queries"

const THREAD_ID = "1dd69115-f4b9-507f-b4d5-9f355f9f5ba0"
const STATE_PATH = `/dashboard/api/threads/${THREAD_ID}/state`

function setReadyState(value: DocumentReadyState) {
  Object.defineProperty(document, "readyState", { value, configurable: true })
}

function run(script: string) {
  new Function(script)()
}

function absolute(url: string): string {
  return new URL(url, location.href).href
}

function stubFetch() {
  const original = vi.fn((_input: RequestInfo | URL, _init?: RequestInit) =>
    Promise.resolve(new Response("{}"))
  )
  vi.stubGlobal("fetch", original)
  return original
}

/** What the app itself requests, captured through the real api client. */
async function recordedSidebarUrl(params: {
  activeThreadId?: string
  includeAutomations: boolean
}): Promise<string> {
  const spy = stubFetch()
  await agentsApi
    .listSidebarThreads({
      activeLimit: SIDEBAR_ACTIVE_LIMIT,
      resolvedLimit: SIDEBAR_RESOLVED_LIMIT,
      activeThreadId: params.activeThreadId,
      includeAutomations: params.includeAutomations,
    })
    .catch(() => undefined)
  const called = spy.mock.calls[0]?.[0]
  vi.unstubAllGlobals()
  return absolute(String(called))
}

// jsdom in this setup exposes no `localStorage`, so the preference the warmup
// reads is stubbed rather than written.
function stubPrefs(prefs: unknown) {
  vi.stubGlobal("localStorage", {
    getItem: () => (prefs === undefined ? null : JSON.stringify(prefs)),
  })
}

afterEach(() => {
  vi.unstubAllGlobals()
  setReadyState("complete")
})

describe("apiWarmupScript", () => {
  it("only matches the routes that render a sidebar or transcript", () => {
    expect(apiWarmupScript("/")).toBeNull()
    expect(apiWarmupScript("/login")).toBeNull()
    expect(apiWarmupScript("/agents/threads")).toBeNull()
    expect(apiWarmupScript(`/agents/local/${THREAD_ID}`)).toBeNull()
    expect(apiWarmupScript(`/agents/${THREAD_ID}/plan`)).toBeNull()
    expect(apiWarmupScript("/agents")).toContain("/threads/sidebar")
    expect(apiWarmupScript(`/agents/${THREAD_ID}`)).toContain(STATE_PATH)
  })

  it("warms only the sidebar on the agents home", () => {
    setReadyState("loading")
    const original = stubFetch()

    run(apiWarmupScript("/agents")!)

    expect(original).toHaveBeenCalledTimes(1)
    expect(String(original.mock.calls[0]?.[0])).toContain("/threads/sidebar")
  })

  it("warms both state and sidebar on a thread route, and hands each over once", async () => {
    setReadyState("loading")
    const original = stubFetch()

    run(apiWarmupScript(`/agents/${THREAD_ID}`)!)
    expect(original).toHaveBeenCalledTimes(2)

    const warmedState = await window.fetch(absolute(STATE_PATH))
    const sidebarUrl = String(
      original.mock.calls.find((c) =>
        String(c[0]).includes("/threads/sidebar")
      )?.[0]
    )
    const warmedSidebar = await window.fetch(sidebarUrl)

    expect(warmedState).toBeInstanceOf(Response)
    expect(warmedSidebar).toBeInstanceOf(Response)
    // Both served from the warm pool, so no extra network calls.
    expect(original).toHaveBeenCalledTimes(2)
    // Pool drained → the patch removes itself.
    expect(window.fetch).toBe(original)
  })

  it("passes unrelated requests through", async () => {
    setReadyState("loading")
    const original = stubFetch()

    run(apiWarmupScript("/agents")!)
    await window.fetch("/dashboard/api/options")

    expect(original).toHaveBeenCalledTimes(2)
    expect(String(original.mock.calls[1]?.[0])).toBe("/dashboard/api/options")
  })

  it("is inert once the document has parsed", () => {
    setReadyState("complete")
    const original = stubFetch()

    run(apiWarmupScript("/agents")!)

    expect(original).not.toHaveBeenCalled()
    expect(window.fetch).toBe(original)
  })

  // The warmed URL is hand-built from the same params the query passes, so it
  // has to be checked against the request the api client actually makes —
  // a mismatch would silently fetch the sidebar twice.
  it.each([
    { includeAutomations: false, prefs: undefined },
    {
      includeAutomations: true,
      prefs: { filters: { includeAutomations: true } },
    },
    {
      includeAutomations: true,
      prefs: { filters: { sources: ["schedule"] } },
    },
  ])(
    "warms the exact sidebar URL the app requests (automations: $includeAutomations)",
    async ({ includeAutomations, prefs }) => {
      const expected = await recordedSidebarUrl({ includeAutomations })

      setReadyState("loading")
      stubPrefs(prefs)
      const original = stubFetch()
      run(apiWarmupScript("/agents")!)

      expect(String(original.mock.calls[0]?.[0])).toBe(expected)
    }
  )

  it("warms the exact sidebar URL the app requests on a thread route", async () => {
    const expected = await recordedSidebarUrl({
      activeThreadId: THREAD_ID,
      includeAutomations: false,
    })

    setReadyState("loading")
    const original = stubFetch()
    run(apiWarmupScript(`/agents/${THREAD_ID}`)!)

    const sidebarCall = original.mock.calls.find((c) =>
      String(c[0]).includes("/threads/sidebar")
    )
    expect(String(sidebarCall?.[0])).toBe(expected)
  })
})
