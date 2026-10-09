/** @vitest-environment jsdom */

import { type QueryClient, QueryClientProvider } from "@tanstack/react-query"
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react"
import { afterEach, beforeEach, expect, it, vi } from "vitest"

import { api, type ReviewStyle } from "@/lib/api"
import { reportError } from "@/lib/errorReporting"
import { makeQueryClient } from "@/lib/query"
import { ReviewStylesPanel } from "./ReviewStylesPanel"

vi.mock("@/lib/errorReporting", () => ({ reportError: vi.fn() }))
vi.mock("@/lib/session", () => ({
  useSession: () => ({ data: { login: "me", is_admin: true } }),
}))
vi.mock("@/lib/profile", () => ({
  useRepos: () => ({ data: { repositories: [] }, isError: false }),
}))

const style = (full_name: string, fields: Partial<ReviewStyle> = {}) =>
  ({
    full_name,
    custom_prompt: `${full_name} prompt`,
    approval_mode: null,
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
  client = makeQueryClient()
  client.setDefaultOptions({ queries: { retry: false } })
  vi.spyOn(api, "listReviewStyles").mockResolvedValue([
    style("acme/api"),
    style("acme/web"),
  ])
  vi.spyOn(api, "getReviewStyle").mockImplementation(async (name) =>
    style(name)
  )
  vi.spyOn(api, "getApprovalsFile").mockResolvedValue({ found: true })
})

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

const repoChip = (repo: string) =>
  screen.queryByRole("button", { name: new RegExp(`^${repo}`) })

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

it("removes a repo from the list immediately and restores it when deletion fails", async () => {
  vi.spyOn(window, "confirm").mockReturnValue(true)
  const request = deferred<void>()
  vi.spyOn(api, "deleteReviewStyle").mockReturnValue(request.promise)
  await renderAndSelect("acme/api")

  fireEvent.click(screen.getByRole("button", { name: "Remove" }))
  await waitFor(() => expect(repoChip("acme/api")).toBeNull())

  const failure = new Error("cannot delete")
  request.reject(failure)
  await waitFor(() => expect(repoChip("acme/api")).not.toBeNull())
  expect(reportError).toHaveBeenCalledWith(
    expect.objectContaining({
      title: "Couldn't remove repository",
      error: failure,
    })
  )
})

it("keeps the saved prompt in the editor once the save settles", async () => {
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
  await waitFor(() => expect(client.isFetching() + client.isMutating()).toBe(0))
  expect(screen.getByDisplayValue("sharper prompt")).toBeTruthy()
})

it("shows the dry-run default and saves an admin's approval mode", async () => {
  const save = vi
    .spyOn(api, "saveReviewApprovalMode")
    .mockImplementation(async (name, approval_mode) =>
      style(name, { approval_mode })
    )
  await renderAndSelect("acme/api")
  expect(await screen.findByText(/found on the default branch/)).toBeTruthy()

  const mode = screen.getByRole("combobox", { name: "Approval mode" })
  await waitFor(() => expect(mode.hasAttribute("disabled")).toBe(false))
  expect(mode.textContent).toContain("Dry run")
  fireEvent.click(mode)
  const approve = await screen.findByRole("option", { name: "Approve" })
  fireEvent.pointerDown(approve)
  fireEvent.click(approve)

  await waitFor(() => expect(save).toHaveBeenCalledWith("acme/api", "approve"))
  await waitFor(() => expect(mode.textContent).toContain("Approve"))
})

it("says when the repository has no .open-swe/APPROVALS.md", async () => {
  vi.spyOn(api, "getApprovalsFile").mockResolvedValue({ found: false })
  await renderAndSelect("acme/api")
  expect(
    await screen.findByText(/reviews post no approval assessment/)
  ).toBeTruthy()
})
