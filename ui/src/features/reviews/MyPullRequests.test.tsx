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
    myPullRequestDetails: vi.fn(),
    repos: vi.fn(),
    reviewSummaries: vi.fn(),
    fixPullRequest: vi.fn(),
  },
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
    .map((row) => within(row).getByText(/^Change/).textContent)

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
  it("keeps titles and numbers plain and provides explicit destination links", async () => {
    mount()
    const title = await screen.findByText("Change 1")
    const row = title.closest("tr")!
    expect(title.closest("a")).toBeNull()
    expect(
      screen.getByRole("columnheader", { name: "Repository" })
    ).toBeTruthy()
    expect(within(row).getAllByRole("cell")[1]?.textContent).toBe("acme/app")
    expect(screen.queryByText("feature/example")).toBeNull()
    expect(within(row).getByText("#1").closest("a")).toBeNull()
    expect(
      within(row).getByRole("link", { name: "GitHub" }).getAttribute("href")
    ).toBe("https://github.com/acme/app/pull/1")
    expect(
      within(row)
        .getByRole("link", { name: "Review Mode" })
        .getAttribute("href")
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
    expect(
      screen
        .getAllByRole("row")
        .slice(1)
        .map(
          (row) =>
            within(row).getAllByRole("cell")[4]?.firstElementChild?.textContent
        )
    ).toEqual(["Pending", "Pending", "Draft", "Conflicted"])
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
    const rows = screen.getAllByRole("row").slice(1)
    for (const row of rows)
      expect(
        within(row).getAllByRole("cell")[4]?.firstElementChild?.textContent
      ).toBe("Draft")
    expect(
      within(rows[0]!).getByRole("button", { name: "Fix in Open SWE" })
    ).toBeTruthy()
    expect(
      within(rows[1]!).getByRole("button", { name: "Fix in Open SWE" })
    ).toBeTruthy()
    expect(
      within(rows[2]!).queryByRole("button", { name: "Fix in Open SWE" })
    ).toBeNull()
  })

  it("hides the review issues column and banner when the review backend is unavailable", async () => {
    vi.mocked(api.reviewSummaries).mockRejectedValue(
      new Error("Review records require the backend")
    )
    mount()
    await screen.findByText("Change 1")
    await waitFor(() =>
      expect(
        screen.queryByRole("columnheader", { name: "Review issues" })
      ).toBeNull()
    )
    expect(screen.queryByText(/Review records require/)).toBeNull()
    expect(screen.getAllByRole("columnheader")).toHaveLength(7)
    expect(
      within(screen.getAllByRole("row")[1]!).getAllByRole("cell")
    ).toHaveLength(7)
  })
  it("offers only global date sorting and passes it to the server", async () => {
    mount()
    await screen.findByText("Change 1")
    expect(api.myPullRequests).toHaveBeenCalledWith("", "updatedAt", "desc")
    for (const name of ["PR", "Pull request", "Diffstat"]) {
      expect(screen.queryByRole("button", { name })).toBeNull()
    }
    fireEvent.click(screen.getByRole("button", { name: /Created/ }))
    await waitFor(() =>
      expect(api.myPullRequests).toHaveBeenCalledWith("", "createdAt", "asc")
    )
    fireEvent.click(screen.getByRole("button", { name: /Created/ }))
    await waitFor(() =>
      expect(api.myPullRequests).toHaveBeenCalledWith("", "createdAt", "desc")
    )
    fireEvent.click(screen.getByRole("button", { name: /Last updated/ }))
    await waitFor(() =>
      expect(api.myPullRequests).toHaveBeenCalledWith("", "updatedAt", "asc")
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
    expect(screen.getAllByRole("row")).toHaveLength(11)
    await waitFor(() =>
      expect(api.myPullRequestDetails).toHaveBeenCalledTimes(10)
    )
    fireEvent.click(screen.getByRole("button", { name: "Next" }))
    await screen.findByText("Change 11")
    expect(screen.queryByText("Change 1")).toBeNull()
    expect(screen.getAllByRole("row")).toHaveLength(3)
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
    fireEvent.click(screen.getByLabelText("Filter by repository"))
    expect(api.repos).not.toHaveBeenCalled()
    expect(await screen.findAllByRole("menuitemcheckbox")).toHaveLength(2)
    fireEvent.click(
      await screen.findByRole("menuitemcheckbox", { name: "acme/other" })
    )
    await waitFor(() =>
      expect(api.myPullRequests).toHaveBeenLastCalledWith(
        "acme/other",
        "updatedAt",
        "desc"
      )
    )
    fireEvent.click(
      await screen.findByRole("menuitemcheckbox", { name: "acme/app" })
    )
    await waitFor(() =>
      expect(api.myPullRequests).toHaveBeenLastCalledWith(
        "acme/other,acme/app",
        "updatedAt",
        "desc"
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
    expect(
      screen.queryByRole("columnheader", { name: "Lines added" })
    ).toBeNull()
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
            within(row).getAllByRole("cell")[4]?.firstElementChild?.textContent
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

  it("queues the fix in the background and keeps the button disabled after success", async () => {
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
    expect(screen.getByRole("table")).toBeTruthy()
  })
})
