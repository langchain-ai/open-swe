/** @vitest-environment jsdom */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { useState } from "react"
import { api, type OpenPullRequest } from "@/lib/api"
import { MyPullRequests } from "./MyPullRequests"
import type { ReviewsSearch } from "./search"

vi.mock("@/lib/api", () => ({
  api: {
    myPullRequests: vi.fn(),
    repos: vi.fn(),
    reviewSummaries: vi.fn(),
    fixPullRequest: vi.fn(),
  },
}))
const navigate = vi.hoisted(() => vi.fn())
vi.mock("@tanstack/react-router", () => ({ useNavigate: () => navigate }))

const pull = (
  number: number,
  fields: Partial<OpenPullRequest> = {}
): OpenPullRequest => ({
  repo: "acme/app",
  number,
  title: `Change ${number}`,
  draft: false,
  additions: number,
  deletions: 3,
  mergeable: true,
  mergeState: "blocked",
  headSha: "a".repeat(40),
  headRef: "feature/example",
  reviewDecision: "none",
  statusAvailable: true,
  createdAt: `2026-09-0${number}T00:00:00Z`,
  updatedAt: `2026-09-0${4 - number}T00:00:00Z`,
  ci: "passing",
  failingChecks: [],
  pendingChecks: [],
  ...fields,
})
const payload = {
  pullRequests: [
    pull(2, {
      repo: "acme/other",
      ci: "failing",
      failingChecks: ["Browser E2E"],
      mergeable: false,
      mergeState: "dirty",
    }),
    pull(1),
  ],
  truncated: false,
  incomplete: false,
  updatedAt: "2026-09-12T12:00:00Z",
}
function mount() {
  function Harness() {
    const [filters, setFilters] = useState<ReviewsSearch>({})
    return (
      <MyPullRequests
        login="octocat"
        filters={filters}
        onFiltersChange={(changes) =>
          setFilters((previous) => ({ ...previous, ...changes }))
        }
      />
    )
  }
  return render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <Harness />
    </QueryClientProvider>
  )
}
const titles = () =>
  screen
    .getAllByRole("row")
    .slice(1)
    .map(
      (row) => within(row).getByRole("link", { name: /^Change/ }).textContent
    )

beforeEach(() => {
  vi.mocked(api.reviewSummaries).mockResolvedValue({})
  vi.mocked(api.myPullRequests).mockResolvedValue(payload)
  vi.mocked(api.repos).mockResolvedValue({
    installations: [],
    repositories: [],
  })
})
afterEach(() => {
  cleanup()
  vi.resetAllMocks()
})

describe("My PRs", () => {
  it("sorts PR numbers numerically and titles alphabetically in both directions", async () => {
    vi.mocked(api.myPullRequests).mockResolvedValue({
      ...payload,
      pullRequests: [
        pull(10, { title: "Change Alpha" }),
        pull(2, { title: "Change Zebra" }),
      ],
    })
    mount()
    await screen.findByText("Change Alpha")
    fireEvent.click(screen.getByRole("button", { name: "PR" }))
    expect(titles()).toEqual(["Change Zebra", "Change Alpha"])
    fireEvent.click(screen.getByRole("button", { name: "PR" }))
    expect(titles()).toEqual(["Change Alpha", "Change Zebra"])
    fireEvent.click(screen.getByRole("button", { name: /Pull request/ }))
    expect(titles()).toEqual(["Change Alpha", "Change Zebra"])
    fireEvent.click(screen.getByRole("button", { name: /Pull request/ }))
    expect(titles()).toEqual(["Change Zebra", "Change Alpha"])
  })
  it("sorts added lines and both dates in either direction, and shows actionable status", async () => {
    mount()
    await screen.findByText("Change 1")
    expect(titles()).toEqual(["Change 1", "Change 2"])
    expect(screen.getByText("Browser E2E")).toBeTruthy()
    expect(
      within(screen.getByRole("table")).getByText("Conflicted")
    ).toBeTruthy()
    expect(
      within(screen.getByRole("table")).getByText("Reviewable")
    ).toBeTruthy()
    expect(screen.getByLabelText("1 lines added, 3 lines deleted")).toBeTruthy()
    fireEvent.click(screen.getByRole("button", { name: /Last updated/ }))
    expect(titles()).toEqual(["Change 2", "Change 1"])
    fireEvent.click(screen.getByRole("button", { name: /Last updated/ }))
    expect(titles()).toEqual(["Change 1", "Change 2"])
    fireEvent.click(screen.getByRole("button", { name: /Created/ }))
    expect(titles()).toEqual(["Change 1", "Change 2"])
    fireEvent.click(screen.getByRole("button", { name: /Created/ }))
    expect(titles()).toEqual(["Change 2", "Change 1"])
  })

  it("filters conflicts and sends the applied repository to the server", async () => {
    vi.mocked(api.myPullRequests).mockImplementation(async (repo) => ({
      ...payload,
      pullRequests: payload.pullRequests.filter(
        (pr) => !repo || repo.split(",").includes(pr.repo)
      ),
    }))
    mount()
    await screen.findByText("Change 1")
    fireEvent.click(screen.getByLabelText("Filter by status"))
    fireEvent.click(
      await screen.findByRole("menuitemcheckbox", { name: "Conflicted" })
    )
    expect(titles()).toEqual(["Change 2"])
    fireEvent.click(
      screen.getByRole("menuitemcheckbox", { name: "Reviewable" })
    )
    expect(titles()).toEqual(["Change 1", "Change 2"])
    fireEvent.keyDown(screen.getByRole("menu"), { key: "Escape" })
    fireEvent.click(screen.getByLabelText("Filter by repository"))
    expect(api.repos).not.toHaveBeenCalled()
    expect(await screen.findAllByRole("menuitemcheckbox")).toHaveLength(2)
    fireEvent.click(
      await screen.findByRole("menuitemcheckbox", { name: "acme/other" })
    )
    await waitFor(() =>
      expect(api.myPullRequests).toHaveBeenLastCalledWith("acme/other")
    )
    fireEvent.click(
      await screen.findByRole("menuitemcheckbox", { name: "acme/app" })
    )
    await waitFor(() =>
      expect(api.myPullRequests).toHaveBeenLastCalledWith("acme/other,acme/app")
    )
  })

  it("shows refresh progress immediately and retains a labeled stale snapshot on failure", async () => {
    mount()
    await screen.findByText("Change 1")
    let reject!: (error: Error) => void
    vi.mocked(api.myPullRequests).mockImplementationOnce(
      () =>
        new Promise((_resolve, fail) => {
          reject = fail
        })
    )
    fireEvent.click(screen.getByRole("button", { name: "Refresh" }))
    await screen.findByText("Refreshing from GitHub…")
    expect(
      (screen.getByRole("button", { name: "Refresh" }) as HTMLButtonElement)
        .disabled
    ).toBe(true)
    reject(new Error("GitHub unavailable"))
    await screen.findByRole("alert")
    expect(screen.getByRole("alert").textContent).toContain("previous snapshot")
    expect(screen.getByText("Change 1")).toBeTruthy()
  })

  it("cycles diffstat modes and limits inline failures to three", async () => {
    vi.mocked(api.myPullRequests).mockResolvedValue({
      ...payload,
      pullRequests: [
        pull(1, { deletions: 100 }),
        pull(2, {
          ci: "failing",
          failingChecks: ["one", "two", "three", "four", "five"],
        }),
      ],
    })
    mount()
    await screen.findByText("Change 1")
    expect(titles()).toEqual(["Change 2", "Change 1"])
    for (const expected of [
      ["Change 1", "Change 2"],
      ["Change 1", "Change 2"],
      ["Change 2", "Change 1"],
      ["Change 2", "Change 1"],
    ]) {
      fireEvent.click(screen.getByRole("button", { name: /Diffstat/ }))
      expect(titles()).toEqual(expected)
    }
    const more = screen.getByText("+2 more").closest("details")!
    expect(more.open).toBe(false)
    expect(within(more).getByText("four")).toBeTruthy()
    expect(
      screen.queryByRole("columnheader", { name: "Lines added" })
    ).toBeNull()
    expect(
      within(screen.getAllByRole("columnheader")[0]!).getByRole("button", {
        name: "PR",
      })
    ).toBeTruthy()
  })

  it("restores linked bug and flag counts with review progress", async () => {
    vi.mocked(api.reviewSummaries).mockResolvedValue({
      "acme/other#2": null,
      "acme/app#1": {
        thread_id: "review-1",
        owner: "acme",
        repo: "app",
        number: 1,
        title: "Change 1",
        url: "",
        author: "octocat",
        head_ref: "feature/example",
        base_ref: "main",
        head_sha: "",
        watch: false,
        status: "running",
        updated_at: null,
        counts: { open: 5, resolved: 0, dismissed: 0, bugs: 2, flags: 3 },
      },
    })
    mount()
    const link = await screen.findByRole("link", {
      name: "Open review: 2 bugs, 3 flags",
    })
    expect(link.getAttribute("href")).toBe("/agents/reviews/acme/app/1")
    expect(within(link).getByText("Reviewing…")).toBeTruthy()
    expect(screen.getByText("Not reviewed")).toBeTruthy()
  })

  it("prioritizes draft, conflicts and failing checks above review decisions", async () => {
    vi.mocked(api.myPullRequests).mockResolvedValue({
      ...payload,
      pullRequests: [
        pull(1, { draft: true, mergeable: false, ci: "failing" }),
        pull(2, { mergeable: false, ci: "failing" }),
        pull(3, { ci: "failing", reviewDecision: "approved" }),
        pull(4, { reviewDecision: "changes_requested" }),
        pull(5, { reviewDecision: "approved" }),
        pull(6),
      ],
    })
    mount()
    await screen.findByText("Change 6")
    expect(
      screen
        .getAllByRole("row")
        .slice(1)
        .map(
          (row) =>
            within(row).getAllByRole("cell")[3]?.firstElementChild?.textContent
        )
    ).toEqual([
      "Draft",
      "Conflicted",
      "Failing",
      "Changes Requested",
      "Approved",
      "Reviewable",
    ])
  })

  it("opens the fix thread and shows progress while the request runs", async () => {
    let resolve!: (value: { thread_id: string }) => void
    vi.mocked(api.fixPullRequest).mockImplementation(
      () =>
        new Promise((done) => {
          resolve = done
        })
    )
    mount()
    fireEvent.click(
      await screen.findByRole("button", { name: "Fix in Open SWE" })
    )
    expect(
      (
        (await screen.findByRole("button", {
          name: "Opening thread…",
        })) as HTMLButtonElement
      ).disabled
    ).toBe(true)
    expect(api.fixPullRequest).toHaveBeenCalledWith("acme/other", 2)
    resolve({ thread_id: "fix-thread" })
    await waitFor(() =>
      expect(navigate).toHaveBeenCalledWith({
        to: "/agents/$threadId",
        params: { threadId: "fix-thread" },
      })
    )
  })
})
