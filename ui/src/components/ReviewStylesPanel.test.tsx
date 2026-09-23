/** @vitest-environment jsdom */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react"
import { afterEach, beforeEach, expect, it, vi } from "vitest"

import { api, type ReviewStyle } from "@/lib/api"
import { ReviewStylesPanel } from "./ReviewStylesPanel"

vi.mock("@/lib/session", () => ({
  useSession: () => ({ data: { login: "me", is_admin: true } }),
}))
vi.mock("@/lib/profile", () => ({
  useRepos: () => ({ data: { repositories: [] }, isError: false }),
}))

const style = (full_name: string, fields: Partial<ReviewStyle> = {}) =>
  ({
    full_name,
    status: "completed",
    custom_prompt: `${full_name} prompt`,
    approval_policy: null,
    analysis_summary: null,
    top_reviewers: [],
    prs_sampled: 0,
    reviews_sampled: 0,
    analysis_thread_id: null,
    analysis_run_id: null,
    error: null,
    ...fields,
  }) satisfies ReviewStyle

type Deferred<T> = {
  promise: Promise<T>
  resolve: (value: T) => void
  reject: (error: Error) => void
}

function deferred<T>(): Deferred<T> {
  let resolve: (value: T) => void = () => {}
  let reject: (error: Error) => void = () => {}
  const promise = new Promise<T>((res, rej) => {
    resolve = res
    reject = rej
  })
  return { promise, resolve, reject }
}

let client: QueryClient

beforeEach(() => {
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  vi.spyOn(api, "listReviewStyles").mockResolvedValue([
    style("acme/api"),
    style("acme/web"),
  ])
  vi.spyOn(api, "getReviewStyle").mockImplementation(async (name) =>
    style(name)
  )
})

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

const repoChip = (repo: string) =>
  screen.queryByRole("button", { name: new RegExp(`^${repo}`) })

const chipStatus = (repo: string) =>
  repoChip(repo)?.textContent?.slice(repo.length)

async function renderAndSelect(repo: string) {
  render(
    <QueryClientProvider client={client}>
      <ReviewStylesPanel />
    </QueryClientProvider>
  )
  fireEvent.click(
    await screen.findByRole("button", { name: new RegExp(`^${repo}`) })
  )
  await screen.findByDisplayValue(`${repo} prompt`)
}

it("shows analysis as running before the request returns and reverts on failure", async () => {
  const request = deferred<ReviewStyle>()
  vi.spyOn(api, "analyzeReviewStyle").mockReturnValue(request.promise)
  await renderAndSelect("acme/api")

  fireEvent.click(screen.getByRole("button", { name: "Run analysis" }))
  await screen.findByRole("button", { name: "Analyzing…" })
  expect(chipStatus("acme/api")).toBe("running")

  request.reject(new Error("analysis unavailable"))
  await screen.findByText("analysis unavailable")
  expect(screen.getByRole("button", { name: "Run analysis" })).toBeTruthy()
  expect(chipStatus("acme/api")).toBe("completed")
})

it("caches an analysis under the repo it started for after the selection moves", async () => {
  const request = deferred<ReviewStyle>()
  vi.spyOn(api, "analyzeReviewStyle").mockReturnValue(request.promise)
  await renderAndSelect("acme/api")
  fireEvent.click(screen.getByRole("button", { name: "Run analysis" }))
  await screen.findByRole("button", { name: "Analyzing…" })

  const web = repoChip("acme/web")
  if (!web) throw new Error("acme/web chip missing")
  fireEvent.click(web)
  await screen.findByDisplayValue("acme/web prompt")
  request.resolve(
    style("acme/api", { status: "running", analysis_run_id: "run-1" })
  )

  await waitFor(() =>
    expect(
      client.getQueryData<ReviewStyle>(["reviewStyle", "acme/api"])
        ?.analysis_run_id
    ).toBe("run-1")
  )
  expect(
    client.getQueryData<ReviewStyle>(["reviewStyle", "acme/web"])?.status
  ).toBe("completed")
  expect(
    vi.mocked(api.getReviewStyle).mock.calls.filter(([n]) => n === "acme/web")
  ).toHaveLength(1)
})

it("removes a repo from the list immediately and restores it when deletion fails", async () => {
  vi.spyOn(window, "confirm").mockReturnValue(true)
  const request = deferred<void>()
  vi.spyOn(api, "deleteReviewStyle").mockReturnValue(request.promise)
  await renderAndSelect("acme/api")

  fireEvent.click(screen.getByRole("button", { name: "Remove" }))
  await waitFor(() => expect(repoChip("acme/api")).toBeNull())

  request.reject(new Error("cannot delete"))
  await waitFor(() => expect(repoChip("acme/api")).not.toBeNull())
  expect(screen.getByText("cannot delete")).toBeTruthy()
})

it("keeps the saved prompt without refetching the style", async () => {
  vi.spyOn(api, "saveReviewStylePrompt").mockImplementation(
    async (name, custom_prompt) => style(name, { custom_prompt })
  )
  await renderAndSelect("acme/api")

  fireEvent.change(screen.getByDisplayValue("acme/api prompt"), {
    target: { value: "sharper prompt" },
  })
  fireEvent.click(screen.getByRole("button", { name: "Save prompt" }))

  await waitFor(() =>
    expect(
      client.getQueryData<ReviewStyle>(["reviewStyle", "acme/api"])
        ?.custom_prompt
    ).toBe("sharper prompt")
  )
  expect(screen.getByDisplayValue("sharper prompt")).toBeTruthy()
  expect(api.getReviewStyle).toHaveBeenCalledTimes(1)
})
