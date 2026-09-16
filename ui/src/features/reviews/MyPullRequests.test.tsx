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
import { toast } from "sonner"

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }))
import { api, type OpenPullRequest } from "@/lib/api"
import { MyPullRequests } from "./MyPullRequests"
import type { ReviewsSearch } from "./search"

vi.mock("@/lib/api", () => ({
  api: {
    myPullRequests: vi.fn(),
    myPullRequestDetails: vi.fn(),
    repos: vi.fn(),
    reviewSummaries: vi.fn(),
    fixPullRequest: vi.fn(),
    pullRequestThreadStatus: vi.fn(),
    mergePullRequest: vi.fn(),
    closePullRequest: vi.fn(),
    repoMergeMethods: vi.fn(),
    openPullRequestThread: vi.fn(),
  },
}))
vi.mock("@/lib/session", () => ({
  useSession: () => ({ data: { login: "octocat" } }),
}))
const navigate = vi.hoisted(() => vi.fn())
vi.mock("@tanstack/react-router", () => ({
  useNavigate: () => navigate,
  Link: ({
    params,
    children,
    className,
  }: {
    params: { owner: string; repo: string; number: string }
    children: React.ReactNode
    className?: string
  }) => (
    <a
      className={className}
      href={`/agents/reviews/${params.owner}/${params.repo}/${params.number}`}
    >
      {children}
    </a>
  ),
}))

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
  nextPage: null,
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
const section = () =>
  screen.getByRole("region", { name: "My open pull requests" })
const cards = () =>
  screen.queryAllByLabelText(/^Select PR #/).map((box) => box.closest("li")!)
const statusNames = [
  "Loading…",
  "Could not load status",
  "Draft",
  "Conflicted",
  "Failing",
  "Pending",
  "Status unavailable",
  "Changes Requested",
  "Approved",
  "Reviewable",
] as const
const statuses = (card: HTMLElement) =>
  statusNames.filter((status) => within(card).queryAllByText(status).length > 0)
const titles = () =>
  cards().map((card) => within(card).getByText(/^Change/).textContent)
const bulk = "Bulk pull request actions"
const selectAll = async () => {
  fireEvent.click(screen.getByLabelText("Select all PRs on this page"))
  await screen.findByRole("group", { name: bulk })
}
const bulkButton = (name: string) =>
  within(screen.getByRole("group", { name: bulk })).getByRole("button", {
    name,
  }) as HTMLButtonElement

beforeEach(() => {
  vi.mocked(api.pullRequestThreadStatus).mockResolvedValue({ running: false })
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
  // useRepos seeds its query from localStorage, so leftovers would otherwise
  // supply repository options to the next test.
  window.localStorage.clear()
})

const repoOptions = async () => {
  fireEvent.click(screen.getByLabelText("Filter by repository"))
  return (await screen.findAllByRole("menuitemcheckbox")).map(
    (option) => option.textContent
  )
}
const searchRepos = (text: string) =>
  fireEvent.change(screen.getByLabelText("Search repositories…"), {
    target: { value: text },
  })

describe("My PRs", () => {
  it("does not offer a fix while the associated thread is running", async () => {
    vi.mocked(api.pullRequestThreadStatus).mockResolvedValue({ running: true })
    mount()
    const button = await screen.findByRole("button", {
      name: "Fix in progress",
    })
    expect((button as HTMLButtonElement).disabled).toBe(true)
    expect(screen.queryByRole("button", { name: "Fix" })).toBeNull()
    fireEvent.click(button)
    expect(api.fixPullRequest).not.toHaveBeenCalled()
  })

  it("opens the associated coding thread with immediate loading feedback", async () => {
    let finish!: (result: { thread_id: string }) => void
    vi.mocked(api.openPullRequestThread).mockImplementation(
      () =>
        new Promise((resolve) => {
          finish = resolve
        })
    )
    mount()
    const card = (await screen.findByText("Change 1")).closest("li")!
    fireEvent.click(within(card).getByRole("button", { name: "Agent" }))
    const opening = await within(card).findByRole("button", {
      name: "Opening thread…",
    })
    expect((opening as HTMLButtonElement).disabled).toBe(true)
    expect(api.openPullRequestThread).toHaveBeenCalledWith(
      "acme/app",
      1,
      "Change 1"
    )
    expect(navigate).not.toHaveBeenCalled()
    finish({ thread_id: "coding-thread" })
    await waitFor(() =>
      expect(navigate).toHaveBeenCalledWith({
        to: "/agents/$threadId",
        params: { threadId: "coding-thread" },
      })
    )
  })

  it("keeps the PR page open and allows retry when opening a thread fails", async () => {
    vi.mocked(api.openPullRequestThread).mockRejectedValue(
      new Error("Thread backend unavailable")
    )
    mount()
    const card = (await screen.findByText("Change 1")).closest("li")!
    fireEvent.click(within(card).getByRole("button", { name: "Agent" }))
    expect(await within(card).findByRole("alert")).toHaveProperty(
      "textContent",
      "Thread backend unavailable"
    )
    expect(
      (
        within(card).getByRole("button", {
          name: "Agent",
        }) as HTMLButtonElement
      ).disabled
    ).toBe(false)
    expect(navigate).not.toHaveBeenCalled()
  })

  it("merges in the background and removes only confirmed merges", async () => {
    vi.mocked(api.myPullRequests).mockResolvedValue({
      ...payload,
      pullRequests: [pull(1, { reviewDecision: "approved" }), pull(2)],
    })
    let finish!: (result: { merged: boolean }) => void
    vi.mocked(api.mergePullRequest).mockImplementation(
      () =>
        new Promise((resolve) => {
          finish = resolve
        })
    )
    mount()
    await screen.findByText("Change 1")
    fireEvent.change(
      screen.getByRole("combobox", { name: "Merge method for PR #1" }),
      { target: { value: "squash" } }
    )
    fireEvent.click(screen.getByRole("button", { name: "Merge" }))
    const merging = await screen.findByRole("button", { name: "Merging…" })
    expect((merging as HTMLButtonElement).disabled).toBe(true)
    expect(screen.getByText("Change 1")).toBeTruthy()
    expect(api.mergePullRequest).toHaveBeenCalledWith(
      expect.objectContaining({ number: 1, headSha: "a".repeat(40) }),
      "squash"
    )
    finish({ merged: true })
    await waitFor(() => expect(screen.queryByText("Change 1")).toBeNull())
    expect(screen.getByText("Change 2")).toBeTruthy()
    expect(toast.success).toHaveBeenCalledWith("Merged acme/app#1")
    expect(navigate).not.toHaveBeenCalled()
  })

  it("keeps a rejected merge visible with a retry action", async () => {
    vi.mocked(api.myPullRequests).mockResolvedValue({
      ...payload,
      pullRequests: [pull(1, { reviewDecision: "approved" })],
    })
    vi.mocked(api.mergePullRequest).mockRejectedValue(
      new Error("Required checks have not passed")
    )
    mount()
    await screen.findByText("Change 1")
    fireEvent.change(
      screen.getByRole("combobox", { name: "Merge method for PR #1" }),
      { target: { value: "merge" } }
    )
    fireEvent.click(screen.getByRole("button", { name: "Merge" }))
    expect(await screen.findByRole("alert")).toHaveProperty(
      "textContent",
      "Required checks have not passed"
    )
    expect(screen.getByText("Change 1")).toBeTruthy()
    expect(toast.error).toHaveBeenCalledWith("Could not merge acme/app#1", {
      description: "Required checks have not passed",
    })
    expect(
      (screen.getByRole("button", { name: "Retry merge" }) as HTMLButtonElement)
        .disabled
    ).toBe(false)
  })

  it("keeps titles and numbers plain and provides explicit destination links", async () => {
    mount()
    const title = await screen.findByText("Change 1")
    const card = title.closest("li")!
    expect(title.closest("a")).toBeNull()
    expect(within(card).getByText("acme/app")).toBeTruthy()
    expect(screen.queryByText("feature/example")).toBeNull()
    expect(within(card).getByText("#1").closest("a")).toBeNull()
    expect(
      within(card).getByRole("link", { name: "GitHub" }).getAttribute("href")
    ).toBe("https://github.com/acme/app/pull/1")
    expect(
      within(card).getByRole("link", { name: "Reviewer" }).getAttribute("href")
    ).toBe("/agents/reviews/acme/app/1")
  })
  it("shows pending checks as Pending, preserving draft and conflict priority", async () => {
    vi.mocked(api.myPullRequests).mockResolvedValue({
      ...payload,
      pullRequests: [
        pull(1, { ci: "pending", pendingChecks: ["build"] }),
        pull(2, { ci: "pending", reviewDecision: "approved" }),
        pull(3, { ci: "pending", draft: true }),
        pull(4, { ci: "pending", mergeable: false }),
      ],
    })
    mount()
    await screen.findByText("Change 4")
    expect(cards().map(statuses)).toEqual([
      ["Pending"],
      ["Pending"],
      ["Draft"],
      ["Conflicted"],
    ])
    fireEvent.click(screen.getByLabelText("Filter by status"))
    fireEvent.click(
      await screen.findByRole("menuitemcheckbox", { name: "Pending" })
    )
    expect(titles()).toEqual(["Change 1", "Change 2"])
  })
  it("shows fix actions on conflicted or failing drafts but not healthy drafts", async () => {
    vi.mocked(api.myPullRequests).mockResolvedValue({
      ...payload,
      pullRequests: [
        pull(1, { draft: true, mergeable: false }),
        pull(2, { draft: true, ci: "failing" }),
        pull(3, { draft: true }),
      ],
    })
    mount()
    await screen.findByText("Change 3")
    const shown = cards()
    for (const card of shown)
      expect(within(card).getByText("Draft")).toBeTruthy()
    expect(within(shown[0]!).getByText("Conflicted")).toBeTruthy()
    expect(within(shown[1]!).getByText("Failing")).toBeTruthy()
    expect(within(shown[2]!).queryByText("Conflicted")).toBeNull()
    expect(within(shown[0]!).getByRole("button", { name: "Fix" })).toBeTruthy()
    expect(within(shown[1]!).getByRole("button", { name: "Fix" })).toBeTruthy()
    expect(within(shown[2]!).queryByRole("button", { name: "Fix" })).toBeNull()
  })

  it("hides the review node and banner when the review backend is unavailable", async () => {
    vi.mocked(api.reviewSummaries).mockRejectedValue(
      new Error("Review records require the backend")
    )
    mount()
    await screen.findByText("Change 1")
    await waitFor(() =>
      expect(screen.queryByText("Loading review…")).toBeNull()
    )
    expect(screen.queryByText(/Review records require/)).toBeNull()
    expect(cards()).toHaveLength(2)
    for (const card of cards()) {
      expect(within(card).queryByText("Review unavailable")).toBeNull()
      expect(within(card).queryByText("Not reviewed")).toBeNull()
      expect(
        within(card).queryByRole("link", { name: /Open review/ })
      ).toBeNull()
    }
  })
  it("offers only global date sorting and passes it to the server", async () => {
    mount()
    await screen.findByText("Change 1")
    expect(api.myPullRequests).toHaveBeenCalledWith("", "updatedAt", "desc", 1)
    for (const name of ["PR", "Pull request", "Diffstat"]) {
      expect(screen.queryByRole("button", { name })).toBeNull()
    }
    fireEvent.click(screen.getByRole("button", { name: /Created/ }))
    await waitFor(() =>
      expect(api.myPullRequests).toHaveBeenCalledWith("", "createdAt", "asc", 1)
    )
    fireEvent.click(screen.getByRole("button", { name: /Created/ }))
    await waitFor(() =>
      expect(api.myPullRequests).toHaveBeenCalledWith(
        "",
        "createdAt",
        "desc",
        1
      )
    )
    fireEvent.click(screen.getByRole("button", { name: /Last updated/ }))
    await waitFor(() =>
      expect(api.myPullRequests).toHaveBeenCalledWith("", "updatedAt", "asc", 1)
    )
  })

  it("shows ten lightweight rows before their details finish and loads the next page on demand", async () => {
    vi.mocked(api.myPullRequests).mockResolvedValue({
      ...payload,
      pullRequests: Array.from({ length: 12 }, (_, index) =>
        pull(index + 1, { detailsLoading: true, additions: null })
      ),
    })
    let resolve!: (value: OpenPullRequest) => void
    vi.mocked(api.myPullRequestDetails).mockImplementation(
      () =>
        new Promise((done) => {
          resolve = done
        })
    )
    mount()
    await screen.findByText("Change 10")
    expect(screen.queryByText("Change 11")).toBeNull()
    expect(cards()).toHaveLength(10)
    await waitFor(() =>
      expect(api.myPullRequestDetails).toHaveBeenCalledTimes(10)
    )
    fireEvent.click(screen.getByRole("button", { name: "Next" }))
    await screen.findByText("Change 11")
    expect(screen.queryByText("Change 1")).toBeNull()
    expect(cards()).toHaveLength(2)
    await waitFor(() =>
      expect(api.myPullRequestDetails).toHaveBeenCalledTimes(12)
    )
    resolve(pull(12))
    await waitFor(() =>
      expect(
        screen.getByLabelText("12 lines added, 3 lines deleted")
      ).toBeTruthy()
    )
  })

  it("loads the next GitHub page when paging past the loaded rows", async () => {
    vi.mocked(api.myPullRequests).mockImplementation(
      async (_repo, _sort, _direction, page = 1) => ({
        ...payload,
        pullRequests: Array.from({ length: 10 }, (_, index) =>
          pull((page - 1) * 10 + index + 1)
        ),
        nextPage: page === 1 ? 2 : null,
      })
    )
    mount()
    await screen.findByText("Change 10")
    await waitFor(() =>
      expect(api.myPullRequests).toHaveBeenLastCalledWith(
        "",
        "updatedAt",
        "desc",
        2
      )
    )
    await screen.findByText(/10 of 20 PRs/)
    fireEvent.click(screen.getByRole("button", { name: "Next" }))
    await screen.findByText("Change 11")
    expect(screen.queryByText("Change 1")).toBeNull()
    expect(api.myPullRequests).toHaveBeenCalledTimes(2)
  })

  it("shows no PRs and offers a repository filter when the search times out", async () => {
    vi.mocked(api.myPullRequests).mockResolvedValue({
      ...payload,
      pullRequests: [],
      incomplete: true,
    })
    vi.mocked(api.repos).mockResolvedValue({
      installations: [],
      repositories: [
        { full_name: "acme/app", private: false },
        { full_name: "acme/other", private: true },
      ],
    })
    mount()
    expect(await screen.findByRole("status")).toHaveProperty(
      "textContent",
      expect.stringContaining("Filter by repository")
    )
    expect(within(section()).queryAllByRole("listitem")).toHaveLength(0)
    expect(await repoOptions()).toEqual(["acme/app", "acme/other"])
    searchRepos("other")
    expect(
      screen
        .getAllByRole("menuitemcheckbox")
        .map((option) => option.textContent)
    ).toEqual(["acme/other"])
  })

  it("keeps the review verdict while GitHub decides whether the branch merges", async () => {
    vi.mocked(api.myPullRequests).mockResolvedValue({
      ...payload,
      pullRequests: [
        pull(1, {
          reviewDecision: "approved",
          mergeable: null,
          mergeState: "unknown",
        }),
      ],
    })
    mount()
    const card = (await screen.findByText("Change 1")).closest("li")!
    expect(within(card).getByText("Approved")).toBeTruthy()
    expect(within(card).queryByText("Status unavailable")).toBeNull()
    // Merging needs the decision GitHub has not made yet.
    expect(within(card).queryByRole("button", { name: "Merge" })).toBeNull()
    fireEvent.click(screen.getByLabelText("Select PR #1 in acme/app"))
    await screen.findByRole("group", { name: bulk })
    expect(bulkButton("Merge").disabled).toBe(true)
  })

  it("offers both a fix and a merge when only optional checks fail", async () => {
    vi.mocked(api.myPullRequests).mockResolvedValue({
      ...payload,
      pullRequests: [
        pull(1, {
          reviewDecision: "approved",
          mergeState: "unstable",
          ci: "failing",
          failingChecks: ["Browser E2E"],
        }),
      ],
    })
    mount()
    const card = (await screen.findByText("Change 1")).closest("li")!
    expect(within(card).getByText("Failing")).toBeTruthy()
    expect(within(card).getByText("Browser E2E")).toBeTruthy()
    expect(within(card).getByRole("button", { name: "Fix" })).toBeTruthy()
    expect(within(card).getByRole("button", { name: "Merge" })).toBeTruthy()
    await selectAll()
    expect(bulkButton("Merge").disabled).toBe(false)
    expect(bulkButton("Fix").disabled).toBe(false)
  })

  it("shows a draft's conflicts and failing checks alongside Draft", async () => {
    vi.mocked(api.myPullRequests).mockResolvedValue({
      ...payload,
      pullRequests: [
        pull(1, { draft: true, mergeable: false, mergeState: "dirty" }),
        pull(2, {
          repo: "acme/other",
          draft: true,
          ci: "failing",
          failingChecks: ["Browser E2E"],
        }),
        pull(3, { repo: "acme/third", draft: true }),
      ],
    })
    mount()
    const card = async (title: string) =>
      within((await screen.findByText(title)).closest("li")!)
    expect((await card("Change 1")).getByText("Conflicted")).toBeTruthy()
    expect((await card("Change 2")).getByText("Failing")).toBeTruthy()
    for (const title of ["Change 1", "Change 2", "Change 3"]) {
      expect((await card(title)).getByText("Draft")).toBeTruthy()
    }
    // Filtering by Conflicted has to reach the conflicted draft.
    fireEvent.click(screen.getByLabelText("Filter by status"))
    fireEvent.click(
      await screen.findByRole("menuitemcheckbox", { name: "Conflicted" })
    )
    expect(titles()).toEqual(["Change 1"])
  })

  it("does not offer a merge while the checks could not be read", async () => {
    vi.mocked(api.myPullRequests).mockResolvedValue({
      ...payload,
      pullRequests: [
        pull(1, {
          reviewDecision: "approved",
          ci: "unknown",
          headSha: "b".repeat(40),
        }),
      ],
    })
    mount()
    const card = (await screen.findByText("Change 1")).closest("li")!
    expect(within(card).getByText("Approved")).toBeTruthy()
    expect(within(card).queryByRole("button", { name: "Merge" })).toBeNull()
    fireEvent.click(screen.getByLabelText("Select PR #1 in acme/app"))
    await screen.findByRole("group", { name: bulk })
    expect(bulkButton("Merge").disabled).toBe(true)
  })

  it("offers the merge method used last time", async () => {
    vi.mocked(api.myPullRequests).mockResolvedValue({
      ...payload,
      pullRequests: [pull(1, { reviewDecision: "approved" })],
    })
    vi.mocked(api.mergePullRequest).mockResolvedValue({ merged: true })
    mount()
    await screen.findByText("Change 1")
    const select = screen.getByRole("combobox", {
      name: "Merge method for PR #1",
    }) as HTMLSelectElement
    expect(select.value).toBe("")
    fireEvent.change(select, { target: { value: "rebase" } })
    fireEvent.click(screen.getByRole("button", { name: "Merge" }))
    await waitFor(() =>
      expect(window.localStorage.getItem("open-swe.reviews.mergeMethod")).toBe(
        "rebase"
      )
    )
    cleanup()
    vi.mocked(api.myPullRequests).mockResolvedValue({
      ...payload,
      pullRequests: [pull(2, { reviewDecision: "approved" })],
    })
    mount()
    await screen.findByText("Change 2")
    expect(
      (
        screen.getByRole("combobox", {
          name: "Merge method for PR #2",
        }) as HTMLSelectElement
      ).value
    ).toBe("rebase")
  })

  it("rediscovers a reopened PR on manual refresh", async () => {
    vi.mocked(api.myPullRequests).mockResolvedValue({
      ...payload,
      pullRequests: [pull(1, { detailsLoading: true })],
    })
    vi.mocked(api.myPullRequestDetails)
      .mockResolvedValueOnce(null)
      .mockResolvedValue(pull(1))
    mount()
    await waitFor(() => expect(api.myPullRequestDetails).toHaveBeenCalledOnce())
    await waitFor(() => expect(screen.queryByText("Change 1")).toBeNull())
    fireEvent.click(screen.getByRole("button", { name: "Refresh" }))
    expect(await screen.findByText("Change 1")).toBeTruthy()
    await waitFor(() =>
      expect(api.myPullRequestDetails).toHaveBeenCalledTimes(2)
    )
  })

  it("filters conflicts and sends the applied repository to the server", async () => {
    vi.mocked(api.myPullRequests).mockImplementation(async (repo) => ({
      ...payload,
      pullRequests: payload.pullRequests.filter(
        (pr) => !repo || repo.split(",").includes(pr.repo)
      ),
    }))
    vi.mocked(api.repos).mockResolvedValue({
      installations: [],
      repositories: [
        { full_name: "globex/quiet", private: false },
        { full_name: "acme/other", private: true },
        { full_name: "acme/app", private: false },
      ],
    })
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
    expect(titles()).toEqual(["Change 2", "Change 1"])
    fireEvent.keyDown(screen.getByRole("menu"), { key: "Escape" })
    // Options come from the accessible repository list, so globex/quiet is
    // offered even though no PR names it.
    expect(await repoOptions()).toEqual([
      "acme/app",
      "acme/other",
      "globex/quiet",
    ])
    searchRepos("ACME/OT")
    expect(
      screen
        .getAllByRole("menuitemcheckbox")
        .map((option) => option.textContent)
    ).toEqual(["acme/other"])
    fireEvent.click(
      await screen.findByRole("menuitemcheckbox", { name: "acme/other" })
    )
    await waitFor(() =>
      expect(api.myPullRequests).toHaveBeenLastCalledWith(
        "acme/other",
        "updatedAt",
        "desc",
        1
      )
    )
    searchRepos("app")
    fireEvent.click(
      await screen.findByRole("menuitemcheckbox", { name: "acme/app" })
    )
    await waitFor(() =>
      expect(api.myPullRequests).toHaveBeenLastCalledWith(
        "acme/other,acme/app",
        "updatedAt",
        "desc",
        1
      )
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

  it("limits inline failures to three", async () => {
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
    const more = screen.getByText("+2 more").closest("details")!
    expect(more.open).toBe(false)
    expect(within(more).getByText("four")).toBeTruthy()
    expect(screen.queryByText("Lines added")).toBeNull()
    expect(
      screen.getByLabelText("1 lines added, 100 lines deleted")
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

  it("ranks conflicts and failing checks above review decisions, and keeps both on a draft", async () => {
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
    expect(cards().map(statuses)).toEqual([
      ["Draft", "Conflicted", "Failing"],
      ["Conflicted"],
      ["Failing"],
      ["Changes Requested"],
      ["Approved"],
      ["Reviewable"],
    ])
  })

  it("queues the fix in the background and keeps the button disabled after success", async () => {
    let resolve!: (value: { thread_id: string }) => void
    vi.mocked(api.fixPullRequest).mockImplementation(
      () =>
        new Promise((done) => {
          resolve = done
        })
    )
    mount()
    fireEvent.click(await screen.findByRole("button", { name: "Fix" }))
    expect(
      (
        (await screen.findByRole("button", {
          name: "Queuing fix…",
        })) as HTMLButtonElement
      ).disabled
    ).toBe(true)
    expect(api.fixPullRequest).toHaveBeenCalledWith(
      expect.objectContaining(payload.pullRequests[0]!)
    )
    resolve({ thread_id: "fix-thread" })
    const queued = await screen.findByRole("button", { name: "Fix queued" })
    expect((queued as HTMLButtonElement).disabled).toBe(true)
    fireEvent.click(queued)
    expect(api.fixPullRequest).toHaveBeenCalledTimes(1)
    expect(navigate).not.toHaveBeenCalled()
    expect(cards()).toHaveLength(2)
  })

  it("counts row and page selections together", async () => {
    mount()
    await screen.findByText("Change 1")
    expect(screen.queryByRole("group", { name: bulk })).toBeNull()
    fireEvent.click(screen.getByLabelText("Select PR #1 in acme/app"))
    expect(await screen.findByText("1 selected")).toBeTruthy()
    fireEvent.click(screen.getByLabelText("Select PR #2 in acme/other"))
    expect(await screen.findByText("2 selected")).toBeTruthy()
    const toggleAll = () =>
      screen.getByLabelText("Select all PRs on this page") as HTMLInputElement
    expect(toggleAll().checked).toBe(true)
    fireEvent.click(toggleAll())
    await waitFor(() =>
      expect(screen.queryByRole("group", { name: bulk })).toBeNull()
    )
    fireEvent.click(toggleAll())
    expect(await screen.findByText("2 selected")).toBeTruthy()
    fireEvent.click(
      within(screen.getByRole("group", { name: bulk })).getByRole("button", {
        name: "Clear selection",
      })
    )
    await waitFor(() =>
      expect(screen.queryByRole("group", { name: bulk })).toBeNull()
    )
  })

  it("closes every selected pull request after confirmation", async () => {
    vi.mocked(api.closePullRequest).mockResolvedValue({ closed: true })
    mount()
    await screen.findByText("Change 1")
    await selectAll()
    fireEvent.click(bulkButton("Close"))
    const dialog = await screen.findByRole("alertdialog")
    expect(within(dialog).getByText("Close 2 pull requests?")).toBeTruthy()
    expect(within(dialog).getByText("acme/other#2")).toBeTruthy()
    expect(within(dialog).getByText("acme/app#1")).toBeTruthy()
    fireEvent.click(
      within(dialog).getByRole("button", { name: "Close pull requests" })
    )
    await waitFor(() => expect(screen.queryByText("Change 1")).toBeNull())
    expect(screen.queryByText("Change 2")).toBeNull()
    expect(api.closePullRequest).toHaveBeenCalledTimes(2)
    expect(toast.success).toHaveBeenCalledWith("Closed 2 pull requests")
  })

  it("keeps a pull request that could not be closed and reports the partial result", async () => {
    vi.mocked(api.closePullRequest).mockImplementation(async (pr) => {
      if (pr.number === 2) throw new Error("Close rejected")
      return { closed: true }
    })
    mount()
    await screen.findByText("Change 1")
    await selectAll()
    fireEvent.click(bulkButton("Close"))
    fireEvent.click(
      await screen.findByRole("button", { name: "Close pull requests" })
    )
    await waitFor(() => expect(screen.queryByText("Change 1")).toBeNull())
    expect(screen.getByText("Change 2")).toBeTruthy()
    expect(toast.error).toHaveBeenCalledWith("Closed 1 of 2 pull requests", {
      description: "acme/other#2: Close rejected",
    })
  })

  it("disables the bulk fix when the selection mixes fixable and healthy PRs", async () => {
    mount()
    await screen.findByText("Change 1")
    await selectAll()
    const fix = bulkButton("Fix")
    expect(fix.disabled).toBe(true)
    expect(fix.title).toBe("Every selected PR must be conflicted or failing")
    fireEvent.click(fix)
    expect(api.fixPullRequest).not.toHaveBeenCalled()
  })

  it("queues a fix for every selected pull request when all are fixable", async () => {
    vi.mocked(api.myPullRequests).mockResolvedValue({
      ...payload,
      pullRequests: [
        pull(1, { ci: "failing" }),
        pull(2, { repo: "acme/other", mergeable: false }),
      ],
    })
    vi.mocked(api.fixPullRequest).mockResolvedValue({ thread_id: "fix" })
    mount()
    await screen.findByText("Change 2")
    await selectAll()
    expect(bulkButton("Fix").disabled).toBe(false)
    fireEvent.click(bulkButton("Fix"))
    await waitFor(() => expect(api.fixPullRequest).toHaveBeenCalledTimes(2))
    expect(toast.success).toHaveBeenCalledWith(
      "Queued fixes for 2 pull requests"
    )
    expect(screen.getByText("Change 1")).toBeTruthy()
  })

  it("disables the bulk merge unless every selected pull request is approved", async () => {
    mount()
    await screen.findByText("Change 1")
    await selectAll()
    const merge = bulkButton("Merge")
    expect(merge.disabled).toBe(true)
    expect(merge.title).toBe("Every selected PR must be approved and passing")
  })

  it("merges with the single method both repositories allow", async () => {
    vi.mocked(api.myPullRequests).mockResolvedValue({
      ...payload,
      pullRequests: [
        pull(1, { reviewDecision: "approved" }),
        pull(2, { repo: "acme/other", reviewDecision: "approved" }),
      ],
    })
    vi.mocked(api.repoMergeMethods).mockImplementation(async (repo) => ({
      mergeMethods: repo === "acme/app" ? ["squash", "merge"] : ["squash"],
    }))
    vi.mocked(api.mergePullRequest).mockResolvedValue({ merged: true })
    mount()
    await screen.findByText("Change 2")
    await selectAll()
    fireEvent.click(bulkButton("Merge"))
    const dialog = await screen.findByRole("dialog")
    expect(
      await within(dialog).findByText("Merge method: Squash merge")
    ).toBeTruthy()
    expect(
      within(dialog).queryByRole("combobox", { name: "Merge method" })
    ).toBeNull()
    fireEvent.click(
      within(dialog).getByRole("button", { name: "Merge pull requests" })
    )
    await waitFor(() => expect(api.mergePullRequest).toHaveBeenCalledTimes(2))
    expect(api.mergePullRequest).toHaveBeenCalledWith(
      expect.objectContaining({ number: 1 }),
      "squash"
    )
    expect(toast.success).toHaveBeenCalledWith("Merged 2 pull requests")
    await waitFor(() => expect(screen.queryByText("Change 1")).toBeNull())
  })

  it("requires a choice when the selected repositories share two merge methods", async () => {
    vi.mocked(api.myPullRequests).mockResolvedValue({
      ...payload,
      pullRequests: [
        pull(1, { reviewDecision: "approved" }),
        pull(2, { repo: "acme/other", reviewDecision: "approved" }),
      ],
    })
    vi.mocked(api.repoMergeMethods).mockResolvedValue({
      mergeMethods: ["squash", "merge"],
    })
    vi.mocked(api.mergePullRequest).mockResolvedValue({ merged: true })
    mount()
    await screen.findByText("Change 2")
    await selectAll()
    fireEvent.click(bulkButton("Merge"))
    const dialog = await screen.findByRole("dialog")
    const select = await within(dialog).findByRole("combobox", {
      name: "Merge method",
    })
    const confirm = within(dialog).getByRole("button", {
      name: "Merge pull requests",
    }) as HTMLButtonElement
    expect(confirm.disabled).toBe(true)
    expect(
      within(select)
        .getAllByRole("option")
        .map((option) => option.textContent)
    ).toEqual(["Choose a merge method", "Squash merge", "Merge commit"])
    fireEvent.change(select, { target: { value: "merge" } })
    await waitFor(() => expect(confirm.disabled).toBe(false))
    fireEvent.click(confirm)
    await waitFor(() => expect(api.mergePullRequest).toHaveBeenCalledTimes(2))
    expect(api.mergePullRequest).toHaveBeenCalledWith(
      expect.objectContaining({ number: 2 }),
      "merge"
    )
  })
})
