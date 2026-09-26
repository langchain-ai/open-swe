/** @vitest-environment jsdom */
import { QueryClientProvider } from "@tanstack/react-query"
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
vi.mock("@/lib/errorReporting", () => ({ reportError: vi.fn() }))
import {
  api,
  type OpenPullRequest,
  type PullRequestActionResult,
  type PullRequestThreadResult,
} from "@/lib/api"
import { reportError } from "@/lib/errorReporting"
import { makeQueryClient } from "@/lib/query"
import { MyPullRequests } from "./MyPullRequests"
import type { ReviewsSearch } from "./search"

vi.mock("@/lib/api", () => ({
  api: {
    myPullRequests: vi.fn(),
    myPullRequestDetails: vi.fn(),
    repos: vi.fn(),
    reviewSummaries: vi.fn(),
    fixPullRequest: vi.fn(),
    addressPullRequestComments: vi.fn(),
    pullRequestThreadStatus: vi.fn(),
    mergePullRequest: vi.fn(),
    closePullRequest: vi.fn(),
    markPullRequestReady: vi.fn(),
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
  reviewRequired: false,
  statusAvailable: true,
  createdAt: `2026-09-0${number}T00:00:00Z`,
  updatedAt: `2026-09-0${4 - number}T00:00:00Z`,
  ci: "passing",
  failingChecks: [],
  pendingChecks: [],
  missingChecks: [],
  unresolvedThreads: 0,
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
  const client = makeQueryClient()
  client.setDefaultOptions({ queries: { retry: false } })
  return render(
    <QueryClientProvider client={client}>
      <Harness />
    </QueryClientProvider>
  )
}
const expectReported = (title: string, message: string) =>
  waitFor(() =>
    expect(reportError).toHaveBeenCalledWith(
      expect.objectContaining({
        title,
        error: expect.objectContaining({
          message: expect.stringContaining(message),
        }),
      })
    )
  )
const section = () =>
  screen.getByRole("region", { name: "My open pull requests" })
// Anchored on the title heading: a card's own failing-check list also contains
// list items, and the empty state is a list item too.
const cards = () =>
  within(section())
    .queryAllByRole("heading", { level: 3 })
    .map((title) => title.closest("li")!)
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
const mergeSelect = async (number: number) => {
  const select = (await screen.findByRole("combobox", {
    name: `Merge method for PR #${number}`,
  })) as HTMLSelectElement
  await waitFor(() => expect(select.disabled).toBe(false))
  return select
}
const mergeOptions = (select: HTMLSelectElement) =>
  within(select)
    .getAllByRole("option")
    .map((option) => option.textContent)

beforeEach(() => {
  vi.mocked(api.repoMergeMethods).mockResolvedValue({
    mergeMethods: ["squash", "merge", "rebase"],
  })
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
    let finish!: (result: PullRequestThreadResult) => void
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
    finish({ thread_id: "coding-thread", already_running: false })
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
    await expectReported(
      "Couldn't open agent thread",
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

  it("merges in the background and keeps the merged card in place", async () => {
    vi.mocked(api.myPullRequests).mockResolvedValue({
      ...payload,
      pullRequests: [pull(1, { reviewDecision: "approved" }), pull(2)],
    })
    let finish!: (result: PullRequestActionResult) => void
    vi.mocked(api.mergePullRequest).mockImplementation(
      () =>
        new Promise((resolve) => {
          finish = resolve
        })
    )
    mount()
    const card = (await screen.findByText("Change 1")).closest("li")!
    fireEvent.change(await mergeSelect(1), { target: { value: "squash" } })
    fireEvent.click(within(card).getByRole("button", { name: "Merge" }))
    const merging = await screen.findByRole("button", { name: "Merging…" })
    expect((merging as HTMLButtonElement).disabled).toBe(true)
    expect(screen.getByText("Change 1")).toBeTruthy()
    expect(api.mergePullRequest).toHaveBeenCalledWith(
      expect.objectContaining({ number: 1, headSha: "a".repeat(40) }),
      "squash"
    )
    finish({ action: "merge", done: true })
    await waitFor(() =>
      expect(within(card).getByText(/^Merged ·/)).toBeTruthy()
    )
    expect(titles()).toEqual(["Change 1", "Change 2"])
    expect(within(card).queryByRole("button", { name: "Merge" })).toBeNull()
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
    fireEvent.change(await mergeSelect(1), { target: { value: "merge" } })
    fireEvent.click(screen.getByRole("button", { name: "Merge" }))
    await expectReported(
      "Could not merge acme/app#1",
      "Required checks have not passed"
    )
    expect(screen.getByText("Change 1")).toBeTruthy()
    expect(
      (screen.getByRole("button", { name: "Retry merge" }) as HTMLButtonElement)
        .disabled
    ).toBe(false)
  })

  it("opens the preview from the title, keeps the number plain, and provides explicit destination links", async () => {
    mount()
    const title = await screen.findByText("Change 1")
    const card = title.closest("li")!
    // The title opens the preview beside the list; GitHub stays an explicit link.
    expect(title.closest("a")).toBeNull()
    expect(title.closest("button")).toBeTruthy()
    expect(title.closest("h3")).toBeTruthy()
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

  it("reads details for the first rows only, leaving the rest to scrolling", async () => {
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
    resolve(pull(10))
    await waitFor(() =>
      expect(
        screen.getByLabelText("10 lines added, 3 lines deleted")
      ).toBeTruthy()
    )
  })

  it("loads the next GitHub page as soon as the loaded rows run out", async () => {
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
    expect(titles()).toEqual(
      Array.from({ length: 10 }, (_, index) => `Change ${index + 1}`)
    )
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
    // The card offers the merge and lets GitHub reject it.
    expect(within(card).getByRole("button", { name: "Merge" })).toBeTruthy()
  })

  it("names a required check that never reported instead of offering the merge", async () => {
    vi.mocked(api.myPullRequests).mockResolvedValue({
      ...payload,
      pullRequests: [
        pull(1, {
          reviewDecision: "approved",
          missingChecks: ["Lint Final Results"],
        }),
      ],
    })
    mount()
    const card = (await screen.findByText("Change 1")).closest("li")!
    expect(
      within(card).getByText("Required, never reported: Lint Final Results")
    ).toBeTruthy()
    expect(within(card).queryByRole("button", { name: "Merge" })).toBeNull()
    expect(
      within(card).getByRole("button", { name: "Update branch" })
    ).toBeTruthy()
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

  it("still offers a merge while the checks could not be read", async () => {
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
    expect(within(card).getByRole("button", { name: "Merge" })).toBeTruthy()
  })

  it("withholds the merge only where GitHub has already refused it", async () => {
    vi.mocked(api.myPullRequests).mockResolvedValue({
      ...payload,
      pullRequests: [
        pull(1, { mergeable: false, mergeState: "dirty" }),
        pull(2, { repo: "acme/other", draft: true }),
        pull(3, { repo: "acme/third", ci: "failing", failingChecks: ["E2E"] }),
      ],
    })
    mount()
    const card = async (title: string) =>
      within((await screen.findByText(title)).closest("li")!)
    const merge = { name: "Merge" }
    expect((await card("Change 1")).queryByRole("button", merge)).toBeNull()
    expect((await card("Change 2")).queryByRole("button", merge)).toBeNull()
    // A failing check may be one GitHub does not require, so the attempt stands.
    expect((await card("Change 3")).getByRole("button", merge)).toBeTruthy()
  })

  it("offers the merge method used last time", async () => {
    vi.mocked(api.myPullRequests).mockResolvedValue({
      ...payload,
      pullRequests: [pull(1, { reviewDecision: "approved" })],
    })
    vi.mocked(api.mergePullRequest).mockResolvedValue({
      action: "merge",
      done: true,
    })
    mount()
    await screen.findByText("Change 1")
    const select = await mergeSelect(1)
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
    expect((await mergeSelect(2)).value).toBe("rebase")
  })

  it("offers only the merge methods the repository allows", async () => {
    vi.mocked(api.myPullRequests).mockResolvedValue({
      ...payload,
      pullRequests: [
        pull(1, { reviewDecision: "approved" }),
        pull(2, { repo: "acme/other", reviewDecision: "approved" }),
      ],
    })
    vi.mocked(api.repoMergeMethods).mockImplementation(async (repo) => ({
      mergeMethods: repo === "acme/app" ? ["squash"] : ["squash", "merge"],
    }))
    vi.mocked(api.mergePullRequest).mockResolvedValue({
      action: "merge",
      done: true,
    })
    mount()
    await screen.findByText("Change 2")
    const only = await mergeSelect(1)
    expect(mergeOptions(only)).toEqual(["Merge method", "Squash merge"])
    // Nothing left to choose, so the merge is ready without a selection.
    expect(only.value).toBe("squash")
    const pair = await mergeSelect(2)
    expect(mergeOptions(pair)).toEqual([
      "Merge method",
      "Squash merge",
      "Merge commit",
    ])
    expect(pair.value).toBe("")
    fireEvent.click(
      within((await screen.findByText("Change 1")).closest("li")!).getByRole(
        "button",
        { name: "Merge" }
      )
    )
    await waitFor(() =>
      expect(api.mergePullRequest).toHaveBeenCalledWith(
        expect.objectContaining({ number: 1 }),
        "squash"
      )
    )
  })

  it("offers every merge method when the repository settings cannot be read", async () => {
    vi.mocked(api.myPullRequests).mockResolvedValue({
      ...payload,
      pullRequests: [pull(1, { reviewDecision: "approved" })],
    })
    vi.mocked(api.repoMergeMethods).mockRejectedValue(
      new Error("Repository settings unavailable")
    )
    vi.mocked(api.mergePullRequest).mockResolvedValue({
      action: "merge",
      done: true,
    })
    mount()
    await screen.findByText("Change 1")
    const select = await mergeSelect(1)
    expect(mergeOptions(select)).toEqual([
      "Merge method",
      "Squash merge",
      "Merge commit",
      "Rebase merge",
    ])
    expect(screen.queryByRole("alert")).toBeNull()
    fireEvent.change(select, { target: { value: "rebase" } })
    fireEvent.click(screen.getByRole("button", { name: "Merge" }))
    await waitFor(() =>
      expect(api.mergePullRequest).toHaveBeenCalledWith(
        expect.objectContaining({ number: 1 }),
        "rebase"
      )
    )
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
    let resolve!: (value: PullRequestThreadResult) => void
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
    resolve({ thread_id: "fix-thread", already_running: false })
    const queued = await screen.findByRole("button", { name: "Fix queued" })
    expect((queued as HTMLButtonElement).disabled).toBe(true)
    fireEvent.click(queued)
    expect(api.fixPullRequest).toHaveBeenCalledTimes(1)
    expect(navigate).not.toHaveBeenCalled()
    expect(cards()).toHaveLength(2)
  })

  it("closes a single pull request only after confirmation", async () => {
    vi.mocked(api.myPullRequests).mockResolvedValue({
      ...payload,
      pullRequests: [pull(1)],
    })
    vi.mocked(api.closePullRequest).mockResolvedValue({
      action: "close",
      done: true,
    })
    mount()
    const card = (await screen.findByText("Change 1")).closest("li")!
    fireEvent.click(within(card).getByRole("button", { name: "Close" }))
    const dialog = await screen.findByRole("dialog")
    expect(within(dialog).getByText("Close acme/app#1?")).toBeTruthy()
    fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }))
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull())
    expect(api.closePullRequest).not.toHaveBeenCalled()
    expect(screen.getByText("Change 1")).toBeTruthy()
    fireEvent.click(within(card).getByRole("button", { name: "Close" }))
    const reopened = await screen.findByRole("dialog")
    fireEvent.change(within(reopened).getByRole("textbox"), {
      target: { value: "  Superseded by #2  " },
    })
    fireEvent.click(
      within(reopened).getByRole("button", { name: "Close pull request" })
    )
    await waitFor(() =>
      expect(within(card).getByText(/^Closed ·/)).toBeTruthy()
    )
    expect(within(card).queryByRole("button", { name: "Close" })).toBeNull()
    expect(api.closePullRequest).toHaveBeenCalledWith(
      expect.objectContaining({ number: 1 }),
      "Superseded by #2"
    )
    expect(toast.success).toHaveBeenCalledWith("Closed acme/app#1")
  })

  it("marks a draft ready for review from its own card", async () => {
    vi.mocked(api.myPullRequests).mockResolvedValue({
      ...payload,
      pullRequests: [pull(1, { draft: true }), pull(2, { repo: "acme/other" })],
    })
    vi.mocked(api.markPullRequestReady).mockResolvedValue({
      action: "mark-ready",
      done: true,
    })
    mount()
    await screen.findByText("Change 2")
    const shown = cards()
    expect(statuses(shown[0]!)).toEqual(["Draft"])
    expect(
      within(shown[1]!).queryByRole("button", { name: "Mark ready" })
    ).toBeNull()
    fireEvent.click(
      within(shown[0]!).getByRole("button", { name: "Mark ready" })
    )
    await waitFor(() =>
      expect(api.markPullRequestReady).toHaveBeenCalledWith(
        expect.objectContaining({ number: 1, draft: true })
      )
    )
    const marked = await within(shown[0]!).findByRole("button", {
      name: "Marked ready",
    })
    expect((marked as HTMLButtonElement).disabled).toBe(true)
    expect(toast.success).toHaveBeenCalledWith(
      "Marked acme/app#1 ready for review"
    )
  })

  it("surfaces a repository rule violation from a merge the dashboard could not predict", async () => {
    vi.mocked(api.myPullRequests).mockResolvedValue({
      ...payload,
      pullRequests: [pull(1)],
    })
    vi.mocked(api.mergePullRequest).mockRejectedValue(
      new Error(
        "Repository rule violations found — A conversation must be resolved before this pull request can be merged"
      )
    )
    mount()
    const card = (await screen.findByText("Change 1")).closest("li")!
    fireEvent.change(await mergeSelect(1), { target: { value: "squash" } })
    fireEvent.click(within(card).getByRole("button", { name: "Merge" }))
    await expectReported(
      "Could not merge acme/app#1",
      "A conversation must be resolved"
    )
    expect(within(card).getByText("Change 1")).toBeTruthy()
  })

  it("offers addressing comments only when conversations are left to address", async () => {
    vi.mocked(api.myPullRequests).mockResolvedValue({
      ...payload,
      pullRequests: [
        pull(1, { unresolvedThreads: 3 }),
        pull(2, { repo: "acme/other", unresolvedThreads: 0 }),
        pull(3, { repo: "acme/third", unresolvedThreads: null }),
        pull(4, { repo: "acme/fourth", unresolvedThreads: 1 }),
      ],
    })
    mount()
    await screen.findByText("Change 4")
    const shown = cards()
    expect(
      within(shown[0]!).getByText("3 unresolved conversations")
    ).toBeTruthy()
    expect(within(shown[1]!).queryByText(/unresolved conversation/)).toBeNull()
    expect(
      within(shown[2]!).getByText("Conversations unavailable")
    ).toBeTruthy()
    expect(
      within(shown[3]!).getByText("1 unresolved conversation")
    ).toBeTruthy()
    const address = "Address comments"
    expect(
      await within(shown[0]!).findByRole("button", { name: address })
    ).toBeTruthy()
    expect(
      within(shown[1]!).queryByRole("button", { name: address })
    ).toBeNull()
    // An unreadable count cannot hide a real action.
    expect(
      within(shown[2]!).getByRole("button", { name: address })
    ).toBeTruthy()
    expect(
      within(shown[3]!).getByRole("button", { name: address })
    ).toBeTruthy()
  })

  it("queues the comment fixes in the background and keeps the button disabled after success", async () => {
    vi.mocked(api.myPullRequests).mockResolvedValue({
      ...payload,
      pullRequests: [pull(1, { unresolvedThreads: 2 })],
    })
    let resolve!: (value: PullRequestThreadResult) => void
    vi.mocked(api.addressPullRequestComments).mockImplementation(
      () =>
        new Promise((done) => {
          resolve = done
        })
    )
    mount()
    const card = (await screen.findByText("Change 1")).closest("li")!
    fireEvent.click(
      await within(card).findByRole("button", { name: "Address comments" })
    )
    expect(
      (
        (await within(card).findByRole("button", {
          name: "Queuing comment fixes…",
        })) as HTMLButtonElement
      ).disabled
    ).toBe(true)
    expect(api.addressPullRequestComments).toHaveBeenCalledWith(
      expect.objectContaining({ number: 1, unresolvedThreads: 2 })
    )
    resolve({ thread_id: "comments-thread", already_running: false })
    const queued = await within(card).findByRole("button", {
      name: "Comment fixes queued",
    })
    expect((queued as HTMLButtonElement).disabled).toBe(true)
    fireEvent.click(queued)
    expect(api.addressPullRequestComments).toHaveBeenCalledTimes(1)
    expect(toast.success).toHaveBeenCalledWith(
      "Queued comment fixes for acme/app#1"
    )
    expect(navigate).not.toHaveBeenCalled()
  })

  it("reports a run that is already addressing the comments", async () => {
    vi.mocked(api.myPullRequests).mockResolvedValue({
      ...payload,
      pullRequests: [pull(1, { unresolvedThreads: 1 })],
    })
    vi.mocked(api.addressPullRequestComments).mockResolvedValue({
      thread_id: "comments-thread",
      already_running: true,
    })
    mount()
    const card = (await screen.findByText("Change 1")).closest("li")!
    fireEvent.click(
      await within(card).findByRole("button", { name: "Address comments" })
    )
    expect(
      await within(card).findByRole("button", { name: "Addressing comments" })
    ).toBeTruthy()
    expect(toast.success).toHaveBeenCalledWith(
      "Already addressing comments for acme/app#1"
    )
  })

  it("does not address comments while the associated thread is running", async () => {
    vi.mocked(api.pullRequestThreadStatus).mockResolvedValue({ running: true })
    vi.mocked(api.myPullRequests).mockResolvedValue({
      ...payload,
      pullRequests: [pull(1, { unresolvedThreads: 4 })],
    })
    mount()
    const button = await screen.findByRole("button", {
      name: "Addressing comments",
    })
    expect((button as HTMLButtonElement).disabled).toBe(true)
    expect(
      screen.queryByRole("button", { name: "Address comments" })
    ).toBeNull()
    fireEvent.click(button)
    expect(api.addressPullRequestComments).not.toHaveBeenCalled()
  })
})
