/** @vitest-environment jsdom */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { cleanup, renderHook, waitFor } from "@testing-library/react"
import { createElement } from "react"
import { afterEach, describe, expect, it, vi } from "vitest"

import { clearPerfSpans, getPerfSpans } from "@/lib/perf/trace"
import {
  STREAM_PREFERENCE_STORAGE_KEY,
  readPreference,
} from "@/lib/streamPreference"
import { agentsApi } from "./api"
import { apiWarmupScript } from "./apiWarmup"
import { SIDEBAR_PAGE_SIZE, sidebarRecentsParams } from "./queries"
import { useAgentThreadStream } from "./stream/useAgentThreadStream"
import { fetchTranscript } from "./transcript/api"
import type { ChatSort } from "./sidebarPrefs"
import type { ReactNode } from "react"

const THREAD_ID = "1dd69115-f4b9-507f-b4d5-9f355f9f5ba0"
const SUMMARY_PATH = `/dashboard/api/threads/${THREAD_ID}`
const STATE_PATH = `${SUMMARY_PATH}/state`
const TRANSCRIPT_PATH = `${SUMMARY_PATH}/transcript`
const RUNS_PATH = `${SUMMARY_PATH}/runs`
const SIDEBAR_PATH = "/dashboard/api/threads/page"

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

function requestsTo(
  fetch: ReturnType<typeof stubFetch>,
  path: string
): Array<Parameters<typeof window.fetch>> {
  return fetch.mock.calls.filter(
    ([input]) => new URL(requestUrl(input)).pathname === path
  )
}

function runLookups(
  fetch: ReturnType<typeof stubFetch>,
  status: "running" | "pending"
): Array<Parameters<typeof window.fetch>> {
  return requestsTo(fetch, RUNS_PATH).filter(
    ([input]) =>
      new URL(requestUrl(input)).searchParams.get("status") === status
  )
}

function asSent([input, init]: Parameters<typeof window.fetch>) {
  const headers = new Headers(init?.headers)
  const requestId = headers.get("x-request-id")
  if (requestId)
    headers.set(
      "x-request-id",
      requestId.replace(/^req_[0-9a-f-]{36}$/, "req_<uuid>")
    )
  return {
    url: requestUrl(input),
    credentials: init?.credentials,
    headers: Object.fromEntries(headers),
  }
}

/** The sidebar's first page, requested the way the app does: through the real api client. */
async function requestSidebarPage({
  includeAutomations = false,
  includeResolved = false,
  includeSlack = true,
  owned = true,
  repoMode = false,
  sort = repoMode ? "created" : "updated",
}: {
  includeAutomations?: boolean
  includeResolved?: boolean
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

function stubStreamPreference() {
  vi.stubGlobal("localStorage", {
    getItem: (key: string) =>
      key === STREAM_PREFERENCE_STORAGE_KEY ? "true" : null,
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

  it("warms the transcript, the summary and the sidebar on a thread route, and hands each over once", async () => {
    setReadyState("loading")
    const original = stubFetch()

    run(apiWarmupScript(`/agents/${THREAD_ID}`)!)
    expect(original).toHaveBeenCalledTimes(3)
    expect(requestsTo(original, STATE_PATH)).toHaveLength(0)
    expect(requestsTo(original, RUNS_PATH)).toHaveLength(0)

    for (const path of [TRANSCRIPT_PATH, SUMMARY_PATH, SIDEBAR_PATH]) {
      const [warmed] = requestsTo(original, path)
      expect(await window.fetch(requestUrl(warmed![0]))).toBeInstanceOf(
        Response
      )
    }

    expect(original).toHaveBeenCalledTimes(3)
    expect(window.fetch).toBe(original)
  })

  it("warms the summary as the thread page requests it, and hands it to that request", async () => {
    const own = stubFetch()
    await agentsApi.getThread(THREAD_ID)

    setReadyState("loading")
    const network = stubFetch()
    run(apiWarmupScript(`/agents/${THREAD_ID}`)!)
    const warmed = requestsTo(network, SUMMARY_PATH)
    expect(warmed).toHaveLength(1)
    await agentsApi.getThread(THREAD_ID)

    expect(requestsTo(network, SUMMARY_PATH)).toEqual(warmed)
    expect(asSent(warmed[0]!)).toEqual(asSent(own.mock.calls[0]!))
  })

  it("warms the transcript as the transcript view requests it, and hands it to that request", async () => {
    const own = stubFetch()
    await fetchTranscript(THREAD_ID)

    setReadyState("loading")
    const network = stubFetch()
    run(apiWarmupScript(`/agents/${THREAD_ID}`)!)
    const warmed = requestsTo(network, TRANSCRIPT_PATH)
    expect(warmed).toHaveLength(1)
    await fetchTranscript(THREAD_ID)

    expect(requestsTo(network, TRANSCRIPT_PATH)).toEqual(warmed)
    expect(asSent(warmed[0]!)).toEqual(asSent(own.mock.calls[0]!))
  })

  // The state URL comes from the SDK (its `select` list follows `stateKeys`), so
  // only the real stream can say what the page will ask for.
  it("sends the app's own request once a warmed one has failed", async () => {
    setReadyState("loading")
    let status = 404
    const original = vi.fn(() =>
      Promise.resolve(new Response("{}", { status }))
    )
    vi.stubGlobal("fetch", original)
    run(apiWarmupScript(`/agents/${THREAD_ID}`)!)
    await new Promise((resolve) => setTimeout(resolve))
    status = 200

    await expect(fetchTranscript(THREAD_ID)).resolves.toEqual({})
    expect(requestsTo(original, TRANSCRIPT_PATH)).toHaveLength(2)
    expect(window.fetch).toBe(original)
  })

  it.each([null, "false"])(
    "warms the view the app reads when the stored setting is %s",
    (stored) => {
      vi.stubGlobal("localStorage", {
        getItem: (key: string) =>
          key === STREAM_PREFERENCE_STORAGE_KEY ? stored : null,
      })
      setReadyState("loading")
      const original = stubFetch()
      run(apiWarmupScript(`/agents/${THREAD_ID}`)!)

      const warmed = readPreference() ? STATE_PATH : TRANSCRIPT_PATH
      const skipped = readPreference() ? TRANSCRIPT_PATH : STATE_PATH
      expect(requestsTo(original, warmed)).toHaveLength(1)
      expect(requestsTo(original, skipped)).toHaveLength(0)
    }
  )

  it("hands the warmed state and running-run lookup to the thread page's stream under the SDK useStream setting", async () => {
    stubStreamPreference()
    setReadyState("loading")
    const network = stubAgentServer()
    run(apiWarmupScript(`/agents/${THREAD_ID}`)!)
    network.mockClear()
    const asked = vi.fn(window.fetch)
    window.fetch = asked

    mountThreadStream()
    await waitFor(() => expect(runLookups(asked, "pending")).toHaveLength(1))

    expect(
      runLookups(asked, "running"),
      "the stream's lookups, with the read and after it"
    ).toHaveLength(2)
    expect(
      runLookups(network, "running"),
      "only the lookup after the read reaches the server"
    ).toHaveLength(1)
    expect(requestsTo(network, STATE_PATH)).toHaveLength(0)
  })

  it("warms the running-run lookup as the thread stream sends it with its state read", async () => {
    stubStreamPreference()
    const own = stubAgentServer()
    mountThreadStream()
    await waitFor(() => expect(runLookups(own, "pending")).toHaveLength(1))
    const [sentWithRead] = runLookups(own, "running")

    setReadyState("loading")
    const network = stubAgentServer()
    run(apiWarmupScript(`/agents/${THREAD_ID}`)!)

    expect(runLookups(network, "running").map(asSent)).toEqual([
      asSent(sentWithRead!),
    ])
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
      name: "retired source preference ignored",
      expected: {},
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
