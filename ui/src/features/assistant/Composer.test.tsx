/** @vitest-environment jsdom */
import {
  AssistantRuntimeProvider,
  useAuiState,
  useExternalStoreRuntime,
  type ThreadMessage,
} from "@assistant-ui/react"
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react"
import { TooltipProvider } from "@langchain/macaw-components/Tooltip"
import { create } from "zustand"
import { afterEach, beforeEach, expect, it, vi } from "vitest"
import type { ReposPayload } from "@/lib/api"
import { useRecentRepos } from "@/lib/recentRepos"
import { Composer } from "./Composer"

const queries = vi.hoisted(() => ({
  repos: {
    data: undefined as ReposPayload | undefined,
    isPending: true,
  },
}))
const useReposQuery = create(() => queries.repos)

vi.mock("@/lib/profile", () => ({
  useProfile: () => ({ data: { default_repo: "org/default" } }),
  useRepos: () => useReposQuery(),
  useRefreshRepos: () => ({ isPending: false, mutate: vi.fn() }),
}))
vi.mock("@/lib/session", () => ({
  useSession: () => ({ data: { login: "alice" } }),
}))
vi.mock("@/features/agents/lib/provider/useModelOptions", () => ({
  useModelOptions: () => ({ models: [], defaultSelection: null }),
}))
vi.mock("@/features/agents/components/ModelPicker", () => ({
  ModelPicker: () => null,
}))
vi.mock("@/features/agents/lib/queries", () => ({
  useWorkspaceOptions: () => ({ data: { workspaces: [] } }),
}))
vi.mock("./AssistantProvider", () => ({
  useThreadMetadata: () => ({ data: undefined }),
}))

function Configuration() {
  const custom = useAuiState((state) => state.composer.runConfig.custom)
  return (
    <output data-testid="config">{JSON.stringify(custom) ?? "unset"}</output>
  )
}

function Harness({ initialRepo }: { initialRepo?: string | null }) {
  const runtime = useExternalStoreRuntime<ThreadMessage>({
    messages: [],
    onNew: vi.fn(),
  })
  return (
    <TooltipProvider>
      <AssistantRuntimeProvider runtime={runtime}>
        <Composer initialRepo={initialRepo} />
        <Configuration />
      </AssistantRuntimeProvider>
    </TooltipProvider>
  )
}

beforeEach(() => {
  useReposQuery.setState({ data: undefined, isPending: true })
  useRecentRepos.setState({
    byAccount: {
      "alice:github": ["org/missing", "org/archived", "org/recent"],
      "bob:github": ["org/other"],
    },
  })
})
afterEach(() => {
  cleanup()
  useRecentRepos.setState({ byAccount: {} })
})

it("waits for available repositories before preferring recent history, then preserves a cleared selection", () => {
  render(<Harness />)
  expect(screen.getByTestId("config").textContent).toBe("unset")

  act(() => {
    useReposQuery.setState({
      isPending: false,
      data: {
        installations: [],
        repositories: [
          { full_name: "org/default", private: false, archived: false },
          { full_name: "org/archived", private: false, archived: true },
          { full_name: "org/recent", private: false, archived: false },
        ],
      },
    })
  })
  expect(screen.getByTestId("config").textContent).toContain(
    '"repo":"org/recent"'
  )

  fireEvent.click(screen.getByRole("button", { name: "Repository" }))
  fireEvent.click(screen.getByRole("button", { name: "No repository" }))
  act(() => useRecentRepos.getState().remember("alice:github", "org/default"))
  expect(screen.getByTestId("config").textContent).toContain('"repo":null')
  expect(screen.getByTestId("config").textContent).toContain(
    '"repo_explicitly_none":true'
  )
})

it("falls back to the profile default when no recent repository is available", () => {
  useReposQuery.setState({
    isPending: false,
    data: { installations: [], repositories: [] },
  })
  render(<Harness />)
  expect(screen.getByTestId("config").textContent).toContain(
    '"repo":"org/default"'
  )
})

it.each([
  { initialRepo: "org/explicit", expected: { repo: "org/explicit" } },
  { initialRepo: null, expected: { repo_explicitly_none: true } },
])(
  "preserves explicit initial repository $initialRepo while repositories load",
  ({ initialRepo, expected }) => {
    render(<Harness initialRepo={initialRepo} />)
    const config: unknown = JSON.parse(
      screen.getByTestId("config").textContent ?? "null"
    )
    expect(config).toMatchObject(expected)
    expect(config).not.toHaveProperty("repo", "org/default")
  }
)
