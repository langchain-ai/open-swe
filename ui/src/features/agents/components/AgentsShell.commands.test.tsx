/** @vitest-environment jsdom */

import { TooltipProvider } from "@langchain/macaw-components/Tooltip"
import { cleanup, render, screen, fireEvent } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { afterEach, expect, it, vi } from "vitest"

import { SidebarThreadRow } from "./SidebarThreadRow"
import { cloudSidebarThread } from "../lib/sidebarThreads"
import { agentThreadKeys } from "../lib/queries"
import { projectKeys } from "@/features/projects/lib/projects"
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
    emptyList: {
      data: undefined,
      isPending: false,
      isError: false,
      items: [] as Array<AgentThread>,
    },
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
  useRouterState: ({
    select,
  }: {
    select: (state: {
      location: { pathname: string; search: Record<string, unknown> }
    }) => unknown
  }) => select({ location: { pathname: "/agents/thread-1", search: {} } }),
  useSearch: () => undefined,
  useRouter: () => ({ options: { parseSearch: () => ({}) } }),
}))

vi.mock("@/lib/session", () => ({ useSession: () => stub.session }))
vi.mock("@/lib/theme", () => ({ useTheme: () => stub.theme }))

vi.mock("@/features/agents/lib/queries", async (actual) => ({
  ...((await actual()) as object),
  useSidebarActiveThread: ({ activeThreadId }: { activeThreadId?: string }) =>
    activeThreadId ? stub.activeThread : undefined,
  useSidebarPinnedThreads: () => stub.pinned,
  useSidebarRecents: () => stub.emptyList,
  useSidebarRepos: () => stub.emptyList,
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

vi.mock("@/features/agents/lib/desktopProjects", () => ({
  useDesktopProjects: () => stub.projects,
}))

window.matchMedia = ((query: string) => ({
  matches: false,
  media: query,
  addEventListener: vi.fn(),
  removeEventListener: vi.fn(),
})) as unknown as typeof window.matchMedia

afterEach(() => {
  cleanup()
  localStorage.clear()
  stub.emptyList.items = []
  vi.clearAllMocks()
})

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

it("keeps a project thread out of No project even when the unscoped page is cached", () => {
  localStorage.setItem(
    "open-swe.agents.sidebar-prefs",
    JSON.stringify({ organize: "repo" })
  )
  const client = new QueryClient({
    defaultOptions: { queries: { enabled: false, retry: false } },
  })
  const thread: AgentThread = {
    ...stub.activeThread,
    status: "idle",
    projectId: "project-1",
    branch: "main",
    model: "gpt-5",
    viewed: true,
    createdAt: 1,
    updatedAt: 2,
    messages: [],
  }
  stub.emptyList.items = [
    {
      ...thread,
      id: "unfiled",
      title: "Unfiled chat",
      projectId: null,
      repo: "",
      repoFullName: "",
    },
  ]
  client.setQueryData(projectKeys.all, [
    {
      id: "project-1",
      name: "Example project",
      workspace: "default",
      repo_full_name: null,
      role: "owner",
      members: [],
      created_at: "2026-09-10T00:00:00Z",
      updated_at: "2026-09-10T00:00:00Z",
    },
  ])
  client.setQueryData(
    agentThreadKeys.infinitePages({
      limit: 10,
      hierarchy: true,
      resolved: false,
      scope: "interactive",
      owned: true,
      sortBy: "created_at",
    }),
    {
      pages: [{ items: [thread], hasMore: false, limit: 10, offset: 0 }],
      pageParams: [0],
    }
  )
  render(
    <QueryClientProvider client={client}>
      <AppCommandProvider>
        <AgentsShell user={USER}>
          <div />
        </AgentsShell>
      </AppCommandProvider>
    </QueryClientProvider>
  )

  expect(screen.getByRole("button", { name: "Example project" })).toBeTruthy()
  expect(screen.getByRole("button", { name: "No project" })).toBeTruthy()
  expect(screen.getByRole("link", { name: /Unfiled chat/ })).toBeTruthy()
  expect(screen.queryByRole("link", { name: /Thread/ })).toBeNull()
})

it("renames an unopened thread from its context menu and discards cancelled drafts", async () => {
  const thread = {
    ...stub.activeThread,
    viewed: true,
  } as unknown as AgentThread
  const client = new QueryClient()
  render(
    <QueryClientProvider client={client}>
      <TooltipProvider>
        <SidebarThreadRow
          item={cloudSidebarThread(thread)}
          isActive={false}
          pinned={false}
          archived={false}
          onTogglePin={stub.noop}
          onToggleArchived={stub.noop}
        />
      </TooltipProvider>
    </QueryClientProvider>
  )
  const row = screen.getByRole("link", { name: /Thread/ })
  fireEvent.contextMenu(row)
  fireEvent.click(await screen.findByRole("menuitem", { name: "Rename" }))
  const input = (await screen.findByRole("textbox", {
    name: "Thread title",
  })) as HTMLInputElement
  expect(document.activeElement).toBe(input)
  expect(input.value).toBe(thread.title)
  expect(input.selectionEnd).toBe(thread.title.length)
  fireEvent.change(input, { target: { value: "Discard this" } })
  fireEvent.keyDown(input, { key: "Escape" })
  expect(stub.rename.mutate).not.toHaveBeenCalled()
  fireEvent.contextMenu(row)
  fireEvent.click(await screen.findByRole("menuitem", { name: "Rename" }))
  const reopened = (await screen.findByRole("textbox", {
    name: "Thread title",
  })) as HTMLInputElement
  expect(reopened.value).toBe(thread.title)
  fireEvent.change(reopened, { target: { value: "   " } })
  expect(
    (screen.getByRole("button", { name: "Save" }) as HTMLButtonElement).disabled
  ).toBe(true)
  fireEvent.change(reopened, { target: { value: "  New title  " } })
  fireEvent.submit(reopened.closest("form")!)
  expect(stub.rename.mutate).toHaveBeenCalledExactlyOnceWith({
    threadId: thread.id,
    title: "New title",
  })
  expect(screen.queryByRole("dialog")).toBeNull()
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
