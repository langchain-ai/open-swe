/** @vitest-environment jsdom */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { cleanup, renderHook, waitFor } from "@testing-library/react"
import { createElement } from "react"
import { afterEach, describe, expect, it, vi } from "vitest"

import { clearPerfSpans, getPerfSpans } from "@/lib/perf/trace"
import { agentsApi } from "./api"
import { apiWarmupScript } from "./apiWarmup"
import { SIDEBAR_PAGE_SIZE, sidebarRecentsParams } from "./queries"
import { useAgentThreadStream } from "./stream/useAgentThreadStream"
import type { ChatSort } from "./sidebarPrefs"
import type { ReactNode } from "react"

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

function requestUrl(input: RequestInfo | URL): string {
  return absolute(input instanceof Request ? input.url : String(input))
}

/** Answers each Agent Server read the thread stream makes on mount. */
function stubAgentServer() {
  const original = vi.fn((input: RequestInfo | URL, _init?: RequestInit) => {
    const body = new URL(requestUrl(input)).pathname.endsWith("/runs")
      ? []
      : { values: { messages: [] }, next: [], tasks: [], metadata: {} }
    return Promise.resolve(
      new Response(JSON.stringify(body), {
        headers: { "content-type": "application/json" },
      })
    )
  })
  vi.stubGlobal("fetch", original)
  return original
}

/** Mounts the thread page's real SDK stream, which reads the thread state on open. */
function mountThreadStream() {
  const queryClient = new QueryClient()
  return renderHook(() => useAgentThreadStream(THREAD_ID), {
    wrapper: ({ children }: { children: ReactNode }) =>
      createElement(QueryClientProvider, { client: queryClient }, children),
  })
}

function stateRequests(fetch: ReturnType<typeof stubAgentServer>) {
  return fetch.mock.calls.filter(
    ([input]) => new URL(requestUrl(input)).pathname === STATE_PATH
  )
}

/** The sidebar's first page, requested the way the app does: through the real api client. */
async function requestSidebarPage({
  includeAutomations = false,
  includeResolved = false,
  includeClaudeCode = true,
  includeSlack = true,
  owned = true,
  repoMode = false,
  sort = repoMode ? "created" : "updated",
}: {
  includeAutomations?: boolean
  includeResolved?: boolean
  includeClaudeCode?: boolean
  includeSlack?: boolean
  owned?: boolean
  repoMode?: boolean
  sort?: ChatSort
} = {}): Promise<void> {
  await agentsApi
    .listThreadsPage({
      ...sidebarRecentsParams({
        owned,
        repoMode,
        includeAutomations,
        includeResolved,
        includeClaudeCode,
        includeSlack,
        sort,
      }),
      limit: SIDEBAR_PAGE_SIZE,
      offset: 0,
    })
    .catch(() => undefined)
}

// jsdom in this setup exposes no `localStorage`, so the preference the warmup
// reads is stubbed rather than written.
function stubPrefs(prefs: unknown) {
  vi.stubGlobal("localStorage", {
    getItem: () => (prefs === undefined ? null : JSON.stringify(prefs)),
  })
}

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
  clearPerfSpans()
  setReadyState("complete")
})

describe("apiWarmupScript", () => {
  it("only matches the routes that render a sidebar or transcript", () => {
    expect(apiWarmupScript("/")).toBeNull()
    expect(apiWarmupScript("/login")).toBeNull()
    expect(apiWarmupScript(`/agents/${THREAD_ID}/plan`)).toBeNull()
    expect(apiWarmupScript("/agents")).toContain("/threads/page")
    expect(apiWarmupScript(`/agents/${THREAD_ID}`)).toContain(STATE_PATH)
  })

  it("warms only the sidebar on the agents home", () => {
    setReadyState("loading")
    const original = stubFetch()

    run(apiWarmupScript("/agents")!)

    expect(original).toHaveBeenCalledTimes(1)
    expect(String(original.mock.calls[0]?.[0])).toContain("/threads/page")
  })

  it("warms both state and sidebar on a thread route, and hands each over once", async () => {
    setReadyState("loading")
    const original = stubFetch()

    run(apiWarmupScript(`/agents/${THREAD_ID}`)!)
    expect(original).toHaveBeenCalledTimes(2)

    const warmedUrl = (path: string) =>
      String(original.mock.calls.find((c) => String(c[0]).includes(path))?.[0])
    const warmedState = await window.fetch(warmedUrl(STATE_PATH))
    const warmedSidebar = await window.fetch(warmedUrl("/threads/page"))

    expect(warmedState).toBeInstanceOf(Response)
    expect(warmedSidebar).toBeInstanceOf(Response)
    // Both served from the warm pool, so no extra network calls.
    expect(original).toHaveBeenCalledTimes(2)
    // Pool drained → the patch removes itself.
    expect(window.fetch).toBe(original)
  })

  // The state URL comes from the SDK (its `select` list follows `stateKeys`), so
  // only the real stream can say what the page will ask for.
  it("hands the warmed thread state to the thread page's stream", async () => {
    setReadyState("loading")
    const original = stubAgentServer()
    run(apiWarmupScript(`/agents/${THREAD_ID}`)!)

    mountThreadStream()
    // The stream lists the thread's runs once it has read the state.
    await waitFor(() =>
      expect(
        original.mock.calls.some(([input]) =>
          new URL(requestUrl(input)).pathname.endsWith("/runs")
        )
      ).toBe(true)
    )

    expect(stateRequests(original)).toHaveLength(1)
  })

  it("times a completed warmup from its start to its response, not its later handoff", async () => {
    setReadyState("loading")
    const now = vi.spyOn(performance, "now").mockReturnValue(100)
    let resolveResponse!: (response: Response) => void
    const original = vi.fn(
      () =>
        new Promise<Response>((resolve) => {
          resolveResponse = resolve
        })
    )
    vi.stubGlobal("fetch", original)
    run(apiWarmupScript("/agents")!)
    now.mockReturnValue(180)
    resolveResponse(new Response("{}"))
    await Promise.resolve()
    now.mockReturnValue(500)

    await agentsApi.listThreadsPage(
      {
        ...sidebarRecentsParams({
          owned: true,
          repoMode: false,
          sort: "updated",
        }),
        limit: SIDEBAR_PAGE_SIZE,
        offset: 0,
      },
      true
    )

    expect(original).toHaveBeenCalledTimes(1)
    expect(getPerfSpans().at(-1)).toMatchObject({
      name: "thread_list_request",
      startedAt: 100,
      duration: 80,
      attributes: { endpoint: "page", status: 200, outcome: "success" },
    })
  })

  it("uses the original start for a warmup still in flight at handoff", async () => {
    setReadyState("loading")
    const now = vi.spyOn(performance, "now").mockReturnValue(100)
    let resolveResponse!: (response: Response) => void
    const original = vi.fn(
      () =>
        new Promise<Response>((resolve) => {
          resolveResponse = resolve
        })
    )
    vi.stubGlobal("fetch", original)
    run(apiWarmupScript("/agents")!)
    now.mockReturnValue(150)
    const page = agentsApi.listThreadsPage(
      {
        ...sidebarRecentsParams({
          owned: true,
          repoMode: false,
          sort: "updated",
        }),
        limit: SIDEBAR_PAGE_SIZE,
        offset: 0,
      },
      true
    )
    now.mockReturnValue(220)
    resolveResponse(new Response("{}"))
    await page

    expect(original).toHaveBeenCalledTimes(1)
    expect(getPerfSpans().at(-1)).toMatchObject({
      name: "thread_list_request",
      startedAt: 100,
      duration: 120,
      attributes: { endpoint: "page", status: 200, outcome: "success" },
    })
  })

  it("does not attribute an unmatched warmup to a different page request", async () => {
    setReadyState("loading")
    const now = vi.spyOn(performance, "now").mockReturnValue(100)
    const original = stubFetch()
    run(apiWarmupScript("/agents")!)
    now.mockReturnValue(500)

    await agentsApi.listThreadsPage({ limit: 123 }, true)

    expect(original).toHaveBeenCalledTimes(2)
    expect(getPerfSpans().at(-1)).toMatchObject({
      name: "thread_list_request",
      startedAt: 500,
      attributes: { endpoint: "page" },
    })
  })

  it("hands over a warmed request whose query keys come in a different order", async () => {
    setReadyState("loading")
    const original = stubFetch()
    run(apiWarmupScript("/agents")!)

    const reordered = new URL(
      String(original.mock.calls[0]?.[0]),
      location.href
    )
    const entries = [...reordered.searchParams].reverse()
    reordered.search = new URLSearchParams(entries).toString()
    await window.fetch(reordered.href)

    expect(original).toHaveBeenCalledTimes(1)
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

  // The warmed query is hand-built from the stored preferences, so it has to be
  // checked against the request the api client actually makes: a mismatch
  // would silently fetch the sidebar twice.
  it.each([
    { name: "defaults", expected: {}, prefs: undefined },
    {
      name: "date mode pages by last activity",
      expected: { sort: "updated" as ChatSort },
      prefs: { organize: "date" },
    },
    {
      name: "all owners explicitly selected",
      expected: { owned: false },
      prefs: { filters: { ownedOnly: false } },
    },
    {
      name: "automations included",
      expected: { includeAutomations: true },
      prefs: { filters: { includeAutomations: true } },
    },
    {
      name: "schedule source selected",
      expected: { includeAutomations: true },
      prefs: { filters: { sources: ["schedule"] } },
    },
    {
      name: "repo mode",
      expected: { repoMode: true },
      prefs: { organize: "repo" },
    },
    {
      name: "resolved included",
      expected: { includeResolved: true },
      prefs: { filters: { includeResolved: true } },
    },
    {
      name: "Claude Code sessions hidden",
      expected: { includeClaudeCode: false },
      prefs: { filters: { includeClaudeCode: false } },
    },
    {
      name: "repo mode ignores the legacy last-update sort preference",
      expected: { repoMode: true, sort: "created" as ChatSort },
      prefs: { organize: "repo", sortChats: "updated" },
    },
    {
      name: "Slack threads hidden",
      expected: { includeSlack: false },
      prefs: { filters: { includeSlack: false } },
    },
    {
      name: "Slack and Claude Code hidden",
      expected: { includeSlack: false, includeClaudeCode: false },
      prefs: { filters: { includeSlack: false, includeClaudeCode: false } },
    },
  ])(
    "hands the warmed sidebar page to the app's request ($name)",
    async ({ expected: params, prefs }) => {
      setReadyState("loading")
      stubPrefs(prefs)
      const original = stubFetch()
      run(apiWarmupScript("/agents")!)

      await requestSidebarPage(params)

      expect(original).toHaveBeenCalledTimes(1)
    }
  )

  it("hands the warmed sidebar page to the app's request on a thread route", async () => {
    setReadyState("loading")
    const original = stubFetch()
    run(apiWarmupScript(`/agents/${THREAD_ID}`)!)

    await requestSidebarPage()

    const sidebarRequests = original.mock.calls.filter((c) =>
      String(c[0]).includes("/threads/page")
    )
    expect(sidebarRequests).toHaveLength(1)
  })
})
