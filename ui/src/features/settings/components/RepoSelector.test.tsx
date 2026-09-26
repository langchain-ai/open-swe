/** @vitest-environment jsdom */
import { useState } from "react"
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react"
import { afterEach, beforeEach, expect, it, vi } from "vitest"
import { RepoSelector } from "./RepoSelector"
import { useRecentRepos } from "@/lib/recentRepos"

const session = vi.hoisted(() => ({ login: "alice" }))
vi.mock("@/lib/session", () => ({ useSession: () => ({ data: session }) }))
vi.mock("@/lib/profile", () => ({
  useRefreshRepos: () => ({ mutate: vi.fn(), isPending: false }),
}))
const repos = ["a", "b", "c", "d", "e", "f"].map((name) => ({
  full_name: `org/${name}`,
}))

beforeEach(() => {
  session.login = "alice"
  useRecentRepos.setState({ byAccount: {} })
})
afterEach(cleanup)

function Picker({ multiple = false }: { multiple?: boolean }) {
  const [single, setSingle] = useState<string | null>(null)
  const [many, setMany] = useState<string[]>([])
  return multiple ? (
    <RepoSelector
      multiple
      repos={repos}
      selectedRepos={many}
      onReposChange={setMany}
    />
  ) : (
    <RepoSelector
      repos={repos}
      selectedRepo={single}
      onRepoChange={setSingle}
    />
  )
}

it("persists only five unique selections and keeps multi-select open without auto-selecting", async () => {
  render(<Picker multiple />)
  fireEvent.click(screen.getByRole("button", { name: "Select repository" }))
  for (const repo of repos)
    fireEvent.click(
      await screen.findByRole("menuitemcheckbox", { name: repo.full_name })
    )
  fireEvent.click(screen.getByRole("menuitemcheckbox", { name: "org/b" }))
  fireEvent.click(screen.getByRole("menuitemcheckbox", { name: "org/b" }))
  expect(useRecentRepos.getState().byAccount["alice:github"]).toEqual([
    "org/b",
    "org/f",
    "org/e",
    "org/d",
    "org/c",
  ])
  await useRecentRepos.persist.rehydrate()
  expect(useRecentRepos.getState().byAccount["alice:github"]).toHaveLength(5)
  expect(
    screen
      .getAllByRole("menuitemcheckbox")
      .map((row) => row.getAttribute("aria-label"))
  ).toEqual(["org/b", "org/f", "org/e", "org/d", "org/c", "org/a"])
})

it("auto-selects the latest available repo and respects an explicit clear", async () => {
  useRecentRepos.setState({
    byAccount: { "alice:github": ["org/missing", "org/c"] },
  })
  render(<Picker />)
  fireEvent.click(await screen.findByRole("button", { name: "org/c" }))
  fireEvent.click(await screen.findByRole("button", { name: "No repository" }))
  await waitFor(() =>
    expect(
      screen.getByRole("button", { name: "Select repository" })
    ).toBeTruthy()
  )
})

it("waits for repository options without overwriting an existing selection", async () => {
  useRecentRepos.setState({ byAccount: { "alice:github": ["org/c"] } })
  const change = vi.fn()
  const view = render(<RepoSelector onRepoChange={change} />)
  expect(change).not.toHaveBeenCalled()
  view.rerender(<RepoSelector repos={repos} onRepoChange={change} />)
  await waitFor(() => expect(change).toHaveBeenCalledWith("org/c"))
  cleanup()
  change.mockClear()
  render(
    <RepoSelector repos={repos} selectedRepo="org/a" onRepoChange={change} />
  )
  expect(change).not.toHaveBeenCalled()
})

it("isolates accounts and does not auto-select disabled or multi-select pickers", () => {
  useRecentRepos.setState({ byAccount: { "alice:github": ["org/c"] } })
  session.login = "bob"
  const change = vi.fn()
  const view = render(<RepoSelector repos={repos} onRepoChange={change} />)
  expect(change).not.toHaveBeenCalled()
  session.login = "alice"
  view.rerender(<RepoSelector disabled repos={repos} onRepoChange={change} />)
  expect(change).not.toHaveBeenCalled()
  view.rerender(
    <RepoSelector
      multiple
      repos={repos}
      selectedRepos={[]}
      onReposChange={change}
    />
  )
  expect(change).not.toHaveBeenCalled()
})
