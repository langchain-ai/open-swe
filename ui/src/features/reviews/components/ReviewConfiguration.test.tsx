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

import { api, type WorkspaceSettings } from "@/lib/api"
import { ReviewConfiguration } from "./ReviewConfiguration"

vi.mock("@/lib/profile", () => ({
  useRepos: () => ({
    data: {
      installations: [],
      repositories: [
        { full_name: "acme/widgets", private: true },
        { full_name: "acme/api", private: true },
      ],
    },
    isLoading: false,
  }),
}))

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

const settings: WorkspaceSettings = {
  review_draft_prs: false,
  pr_summaries: true,
  review_trace_links: true,
  org_guidelines: "Shared guidance",
}

it("uses one repository selection across instructions, policy, and automation", async () => {
  vi.spyOn(window, "confirm").mockReturnValue(true)
  vi.spyOn(api, "getInstanceSettings").mockResolvedValue(settings)
  vi.spyOn(api, "listReviewStyles").mockResolvedValue([])
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  render(
    <QueryClientProvider client={client}>
      <ReviewConfiguration initialTab="instructions" canEdit />
    </QueryClientProvider>
  )

  expect(await screen.findByLabelText("Shared review guidelines")).toBeTruthy()
  fireEvent.change(screen.getByLabelText("Review settings scope"), {
    target: { value: "acme/widgets" },
  })
  expect(
    await screen.findByLabelText("Repository review instructions")
  ).toBeTruthy()
  fireEvent.change(screen.getByLabelText("Repository review instructions"), {
    target: { value: "unsaved repository draft" },
  })
  fireEvent.click(screen.getByRole("tab", { name: "Automation" }))
  expect(
    screen.getByRole("link", {
      name: /Manage automatic reviews for acme\/widgets/,
    })
  ).toBeTruthy()
  fireEvent.click(screen.getByRole("tab", { name: "Instructions" }))
  expect(
    screen.getByLabelText("Repository review instructions")
  ).toHaveProperty("value", "unsaved repository draft")
})

it("requires an explicit discard decision before changing scope", async () => {
  vi.spyOn(window, "confirm").mockReturnValue(false)
  vi.spyOn(api, "getInstanceSettings").mockResolvedValue(settings)
  vi.spyOn(api, "listReviewStyles").mockResolvedValue([])
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  render(
    <QueryClientProvider client={client}>
      <ReviewConfiguration initialTab="instructions" canEdit />
    </QueryClientProvider>
  )
  fireEvent.change(await screen.findByLabelText("Shared review guidelines"), {
    target: { value: "unsaved" },
  })
  const selector = screen.getByLabelText("Review settings scope")
  fireEvent.change(selector, { target: { value: "acme/widgets" } })
  expect(selector).toHaveProperty("value", "")
  expect(window.confirm).toHaveBeenCalled()
})

it("changes clean scopes without an unnecessary confirmation", async () => {
  const confirm = vi.spyOn(window, "confirm").mockReturnValue(true)
  vi.spyOn(api, "getInstanceSettings").mockResolvedValue(settings)
  vi.spyOn(api, "listReviewStyles").mockResolvedValue([])
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  render(
    <QueryClientProvider client={client}>
      <ReviewConfiguration initialTab="instructions" canEdit />
    </QueryClientProvider>
  )
  const selector = screen.getByLabelText("Review settings scope")
  fireEvent.change(selector, { target: { value: "acme/widgets" } })
  expect(selector).toHaveProperty("value", "acme/widgets")
  expect(confirm).not.toHaveBeenCalled()
})

it("changes a dirty scope after the user confirms discarding its draft", async () => {
  vi.spyOn(window, "confirm").mockReturnValue(true)
  vi.spyOn(api, "getInstanceSettings").mockResolvedValue(settings)
  vi.spyOn(api, "listReviewStyles").mockResolvedValue([])
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  render(
    <QueryClientProvider client={client}>
      <ReviewConfiguration initialTab="instructions" canEdit />
    </QueryClientProvider>
  )
  fireEvent.change(await screen.findByLabelText("Shared review guidelines"), {
    target: { value: "unsaved" },
  })
  const selector = screen.getByLabelText("Review settings scope")
  fireEvent.change(selector, { target: { value: "acme/widgets" } })
  expect(selector).toHaveProperty("value", "acme/widgets")
})

it("follows an updated URL tab without remounting the selected scope", async () => {
  vi.spyOn(api, "getInstanceSettings").mockResolvedValue(settings)
  vi.spyOn(api, "listReviewStyles").mockResolvedValue([])
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  const rendered = render(
    <QueryClientProvider client={client}>
      <ReviewConfiguration initialTab="instructions" canEdit />
    </QueryClientProvider>
  )
  expect(screen.getByRole("tab", { name: "Instructions" })).toHaveProperty(
    "ariaSelected",
    "true"
  )
  rendered.rerender(
    <QueryClientProvider client={client}>
      <ReviewConfiguration
        initialTab="approval"
        canEdit
        onTabChange={() => undefined}
      />
    </QueryClientProvider>
  )
  await waitFor(() =>
    expect(screen.getByRole("tab", { name: "Approval policy" })).toHaveProperty(
      "ariaSelected",
      "true"
    )
  )
})

it("locks every instance settings control while one section is saving", async () => {
  vi.spyOn(api, "getInstanceSettings").mockResolvedValue(settings)
  vi.spyOn(api, "listReviewStyles").mockResolvedValue([])
  vi.spyOn(api, "saveInstanceSettings").mockImplementation(
    () => new Promise(() => {})
  )
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  render(
    <QueryClientProvider client={client}>
      <ReviewConfiguration initialTab="instructions" canEdit />
    </QueryClientProvider>
  )
  const editor = await screen.findByLabelText("Shared review guidelines")
  fireEvent.change(editor, { target: { value: "new shared draft" } })
  const save = screen.getByRole("button", { name: "Save guidelines" })
  await waitFor(() => expect(save.matches(":disabled")).toBe(false))
  fireEvent.click(save)

  await waitFor(() => {
    expect(editor.matches(":disabled")).toBe(true)
    for (const control of screen.getAllByRole("switch", { hidden: true })) {
      expect(control.hasAttribute("data-disabled")).toBe(true)
    }
  })
})
