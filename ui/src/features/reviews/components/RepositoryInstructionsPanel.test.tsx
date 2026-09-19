// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react"
import { afterEach, expect, it, vi } from "vitest"

import { api, type ReviewStyle } from "@/lib/api"
import { RepositoryInstructionsPanel } from "./RepositoryInstructionsPanel"

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

const style = (custom_prompt: string | null): ReviewStyle => ({
  full_name: "acme/widgets",
  status: "completed",
  custom_prompt,
  analysis_summary: "Learned from recent review feedback.",
  top_reviewers: ["octocat"],
  prs_sampled: 4,
  reviews_sampled: 12,
  analysis_thread_id: null,
  analysis_run_id: null,
  error: null,
})

function renderPanel() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  render(
    <QueryClientProvider client={client}>
      <RepositoryInstructionsPanel repository="acme/widgets" canEdit />
    </QueryClientProvider>
  )
  return client
}

it("resets existing repository instructions when the draft is blank", async () => {
  vi.spyOn(api, "listReviewStyles").mockResolvedValue([style("original")])
  const remove = vi.spyOn(api, "deleteReviewStyle").mockResolvedValue()
  const save = vi.spyOn(api, "saveReviewStylePrompt")

  renderPanel()
  const editor = await screen.findByLabelText("Repository review instructions")
  fireEvent.change(editor, { target: { value: "" } })
  fireEvent.click(screen.getByRole("button", { name: "Reset instructions" }))

  await waitFor(() => expect(remove).toHaveBeenCalledWith("acme/widgets"))
  expect(save).not.toHaveBeenCalled()
  expect(await screen.findByText("Instructions reset.")).toBeTruthy()
})

it("keeps a local draft when polling returns an updated analyzed prompt", async () => {
  vi.spyOn(api, "listReviewStyles").mockResolvedValue([style("original")])
  const client = renderPanel()
  const editor = await screen.findByLabelText("Repository review instructions")
  fireEvent.change(editor, { target: { value: "my unsaved draft" } })

  client.setQueryData(["reviewStyles"], [style("analyzer replacement")])

  expect(editor).toHaveProperty("value", "my unsaved draft")
  expect(
    screen.getByText(/replaces the saved repository instructions/)
  ).toBeTruthy()
})

it("locks the draft while a save is pending", async () => {
  vi.spyOn(api, "listReviewStyles").mockResolvedValue([style("original")])
  let finish: ((saved: ReviewStyle) => void) | undefined
  vi.spyOn(api, "saveReviewStylePrompt").mockImplementation(
    () =>
      new Promise((resolve) => {
        finish = resolve
      })
  )
  renderPanel()
  const editor = await screen.findByLabelText("Repository review instructions")
  fireEvent.change(editor, { target: { value: "save this exact draft" } })
  fireEvent.click(screen.getByRole("button", { name: "Save instructions" }))

  await waitFor(() => expect(editor.matches(":disabled")).toBe(true))
  expect(
    screen.getByRole("button", { name: "Run analysis" }).matches(":disabled")
  ).toBe(true)
  finish?.(style("save this exact draft"))
})

it("locks prompt mutations until analysis and its refresh finish", async () => {
  vi.spyOn(api, "listReviewStyles").mockResolvedValue([style("original")])
  let finish: ((record: ReviewStyle) => void) | undefined
  vi.spyOn(api, "analyzeReviewStyle").mockImplementation(
    () =>
      new Promise((resolve) => {
        finish = resolve
      })
  )
  renderPanel()
  const editor = await screen.findByLabelText("Repository review instructions")
  fireEvent.change(editor, { target: { value: "local draft" } })
  fireEvent.click(screen.getByRole("button", { name: "Run analysis" }))

  await waitFor(() => expect(editor.matches(":disabled")).toBe(true))
  expect(
    screen
      .getByRole("button", { name: "Save instructions" })
      .matches(":disabled")
  ).toBe(true)
  finish?.({ ...style("original"), status: "running" })
})
