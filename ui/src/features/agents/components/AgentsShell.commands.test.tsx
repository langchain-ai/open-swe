/** @vitest-environment jsdom */

import { TooltipProvider } from "@langchain/macaw-components/Tooltip"
import { cleanup, render, screen, fireEvent } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { afterEach, expect, it, vi } from "vitest"

import { SidebarThreadRow } from "./SidebarThreadRow"
import { cloudSidebarThread } from "../lib/sidebarThreads"
import type { AgentThread } from "../lib/types"

import { AgentsShell } from "./AgentsSidebar"
import { AppCommandProvider, useAppCommand } from "@/lib/appCommands"
import type { SessionUser } from "@/lib/api"

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
  Link: ({
    children,
    to,
    params,
    search,
    ...props
  }: {
    children?: React.ReactNode
    to?: string
    params?: { threadId?: string }
    search?: { subagent?: string }
  }) => (
    <a
      {...props}
      href={`${to?.replace("$threadId", params?.threadId ?? "")}${search?.subagent ? `?subagent=${search.subagent}` : ""}`}
    >
      {children}
    </a>
  ),
  useNavigate: () => stub.noop,
  useRouterState: () => undefined,
  useSearch: () => undefined,
  useRouter: () => ({ options: { parseSearch: () => ({}) } }),
}))

vi.mock("@/lib/session", () => ({ useSession: () => stub.session }))
vi.mock("@/lib/theme", () => ({ useTheme: () => stub.theme }))
vi.mock("@/lib/chatRoutes", () => ({
  useChatRoutes: () => ({ home: "/agents", thread: "/agents/$threadId" }),
}))

vi.mock("@/features/agents/lib/queries", async (actual) => ({
  ...((await actual()) as object),
  useSidebarActiveThread: () => stub.activeThread,
  useSidebarPinnedThreads: () => stub.pinned,
  useSidebarRecents: () => stub.emptyList,
  useSidebarRepos: () => stub.emptyList,
  useSidebarRepoThreads: () => stub.emptyList,
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

vi.mock("@/features/agents/lib/legacyLocal", async (actual) => ({
  ...((await actual()) as object),
  useLegacyLocalThreads: () => stub.localThreads,
  useLegacyLocalActivity: () => stub.activity,
  useRefreshLegacyLocalThreads: () => stub.noop,
  useMarkLegacyLocalThreadViewed: () => stub.noop,
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
const USER: SessionUser = {
  login: "alice",
  email: null,
  avatar_url: null,
  is_admin: false,
}
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
        <AgentsShell user={USER} activeThreadId="thread-1">
          <Probe />
        </AgentsShell>
      </AppCommandProvider>
    </QueryClientProvider>
  )

  expect(probeRenders).toBeLessThanOrEqual(RENDER_BUDGET)
})

it("opens asynchronous workers as real conversations and expands the selected worker separately from synchronous subagents", () => {
  const worker = {
    id: "worker",
    title: "Implement change",
    subagents: [
      {
        toolCallId: "worker-sync",
        title: "Worker inspection",
        status: "in_progress",
        subagentType: "general-purpose",
        startedAt: 0,
        endedAt: null,
      },
    ],
    status: "finished",
    viewed: true,
  } as AgentThread
  const parent = {
    id: "parent",
    title: "Coordinate change",
    status: "idle",
    viewed: true,
    repo: "",
    repoFullName: "",
    taskWorkers: [worker],
    subagents: [
      {
        toolCallId: "sync-tool",
        title: "Inspect code",
        status: "in_progress",
        subagentType: "general-purpose",
        startedAt: 0,
        endedAt: null,
      },
    ],
  } as AgentThread
  const client = new QueryClient()
  render(
    <QueryClientProvider client={client}>
      <TooltipProvider>
        <SidebarThreadRow
          item={cloudSidebarThread(parent)}
          isActive={false}
          activeThreadId="worker"
          pinned={false}
          archived={false}
          onDeleteLocal={stub.noop}
          onTogglePin={stub.noop}
          onToggleArchived={stub.noop}
        />
      </TooltipProvider>
    </QueryClientProvider>
  )
  const workerLink = screen.getByRole("link", { name: /Implement change/ })
  expect(workerLink.getAttribute("href")).toBe("/agents/worker")
  expect(workerLink.getAttribute("aria-current")).toBe("page")
  fireEvent.click(screen.getByRole("button", { name: "Hide task workers" }))
  expect(screen.queryByRole("link", { name: /Implement change/ })).toBeNull()
  fireEvent.click(screen.getByRole("button", { name: "Show task workers" }))
  expect(screen.getByRole("link", { name: /Implement change/ })).toBeTruthy()
  const workerSyncToggle = screen.queryByRole("button", {
    name: "Show worker subagents",
  })
  if (workerSyncToggle) fireEvent.click(workerSyncToggle)
  expect(
    screen.getByRole("link", { name: /Worker inspection/ }).getAttribute("href")
  ).toBe("/agents/worker?subagent=worker-sync")
  const syncToggle = screen.queryByRole("button", { name: "Show subagents" })
  if (syncToggle) fireEvent.click(syncToggle)
  expect(
    screen.getByRole("link", { name: /Inspect code/ }).getAttribute("href")
  ).toBe("/agents/parent?subagent=sync-tool")
})
