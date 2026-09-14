/** @vitest-environment jsdom */

import { cleanup, render } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { afterEach, expect, it, vi } from "vitest"

import { AgentsShell } from "./AgentsSidebar"
import { AppCommandProvider, useAppCommand } from "@/lib/appCommands"

// Every mocked hook must return a stable reference, the way the real hooks do.
// A factory that rebuilds its result each render reintroduces the very churn
// this test exists to catch.
const stub = vi.hoisted(() => {
  const fn = () => vi.fn()
  const mutation = () => ({ mutate: fn(), isPending: false })
  return {
    activeThread: {
      id: "thread-1",
      title: "Thread",
      status: "idle",
      resolved: false,
      repoFullName: "langchain-ai/open-swe",
      repo: "open-swe",
      updatedAt: "2026-09-10T00:00:00Z",
    },
    pinned: { data: [] },
    emptyList: { data: undefined, isPending: false, isError: false, items: [] },
    pages: {
      data: undefined,
      hasNextPage: false,
      isFetchingNextPage: false,
      isPending: false,
      isError: false,
      error: null,
      refetch: fn(),
      fetchNextPage: fn(),
    },
    pin: mutation(),
    resolve: mutation(),
    remove: mutation(),
    rename: mutation(),
    noop: fn(),
    pullRequestFor: () => undefined,
    projects: {
      projects: [],
      loaded: true,
      addProject: fn(),
      removeProject: fn(),
    },
    localThreads: { data: [] },
    activity: {},
    session: { data: { login: "octocat" } },
    theme: { toggleTheme: fn() },
  }
})

vi.mock("@tanstack/react-router", () => ({
  Link: ({ children }: { children?: React.ReactNode }) => <a>{children}</a>,
  useNavigate: () => stub.noop,
}))

vi.mock("@/lib/session", () => ({ useSession: () => stub.session }))
vi.mock("@/lib/theme", () => ({ useTheme: () => stub.theme }))

vi.mock("@/features/agents/lib/queries", async (actual) => ({
  ...((await actual()) as object),
  useSidebarActiveThread: () => stub.activeThread,
  useSidebarPinnedThreads: () => stub.pinned,
  useSidebarRecents: () => stub.emptyList,
  useSidebarProjects: () => stub.emptyList,
  useSidebarProjectThreads: () => stub.emptyList,
  useInfiniteThreadsPages: () => stub.pages,
  useSeedAgentThreadDetails: () => stub.noop,
  // Faithful to TanStack Query: a fresh wrapper object every render, with a
  // stable `mutate` inside it.
  usePinAgentThread: () => ({ ...stub.pin }),
  useResolveAgentThread: () => ({ ...stub.resolve }),
  useDeleteAgentThread: () => stub.remove,
  useRenameAgentThread: () => stub.rename,
}))

vi.mock("@/features/agents/lib/prChecks", () => ({
  useSidebarPullRequests: () => stub.pullRequestFor,
}))

vi.mock("@/features/agents/lib/useRunCompletionNotifier", () => ({
  useRunCompletionNotifier: () => {},
}))

vi.mock("@/features/agents/lib/desktopLocal", async (actual) => ({
  ...((await actual()) as object),
  useDesktopLocalThreads: () => stub.localThreads,
  useLocalThreadActivity: () => stub.activity,
  useRefreshLocalThreads: () => stub.noop,
  useMarkLocalThreadViewed: () => stub.noop,
}))

vi.mock("@/features/agents/lib/desktopProjects", () => ({
  useDesktopProjects: () => stub.projects,
}))

window.matchMedia = ((query: string) => ({
  matches: false,
  media: query,
  addEventListener: vi.fn(),
  removeEventListener: vi.fn(),
})) as unknown as typeof window.matchMedia

afterEach(() => cleanup())

// A registration loop never reaches quiescence, so waiting it out would hang
// the run rather than fail it. The probe re-renders on every change to the
// command list, so it aborts the loop itself once the count is clearly wrong.
const RENDER_BUDGET = 25
let probeRenders = 0
function Probe() {
  useAppCommand("archive-thread")
  probeRenders += 1
  if (probeRenders > RENDER_BUDGET) {
    throw new Error(`sidebar commands re-registered ${probeRenders} times`)
  }
  return null
}

it("registers the active thread's commands once and then stops", async () => {
  const client = new QueryClient({
    defaultOptions: { mutations: { retry: false }, queries: { retry: false } },
  })
  render(
    <QueryClientProvider client={client}>
      <AppCommandProvider>
        <AgentsShell user={null} activeThreadId="thread-1">
          <Probe />
        </AgentsShell>
      </AppCommandProvider>
    </QueryClientProvider>
  )

  expect(probeRenders).toBeLessThanOrEqual(RENDER_BUDGET)
})
