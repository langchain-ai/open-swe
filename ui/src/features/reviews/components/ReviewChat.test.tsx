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
import type { ReactNode } from "react"
import {
  ReviewChat,
  ReviewChatComposerProvider,
  useReviewChatComposer,
} from "./ReviewChat"

const mocks = vi.hoisted(() => ({
  approve: vi.fn(),
  getReviewChat: vi.fn(),
  submit: vi.fn(),
  isLoading: false,
}))

vi.mock("@/lib/api", () => ({
  api: {
    approvePullRequest: mocks.approve,
    getReviewChat: mocks.getReviewChat,
  },
  reviewChatApiBase: () => "/review-chat",
}))
vi.mock("@/lib/langgraph-client", () => ({
  createDashboardClient: () => ({}),
  dashboardFetch: vi.fn(),
}))
vi.mock("@langchain/react", () => ({
  StreamProvider: ({ children }: { children: ReactNode }) => children,
  useStreamContext: () => ({
    messages: [],
    isLoading: mocks.isLoading,
    isThreadLoading: false,
    submit: mocks.submit,
  }),
}))

function AddAttachment() {
  const composer = useReviewChatComposer()
  return (
    <button
      type="button"
      onClick={() =>
        composer?.addAttachment({
          id: "attachment",
          path: "src/app.ts",
          lineLabel: "R1",
          language: "ts",
          snippet: "const value = 1",
        })
      }
    >
      Add attachment
    </button>
  )
}

function renderChat({ withAttachmentButton = false } = {}) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return render(
    <QueryClientProvider client={client}>
      <ReviewChatComposerProvider>
        {withAttachmentButton && <AddAttachment />}
        <ReviewChat
          owner="langchain-ai"
          repo="open-swe"
          number={42}
          headSha={"a".repeat(40)}
          reviewed={true}
        />
      </ReviewChatComposerProvider>
    </QueryClientProvider>
  )
}

async function composerInput() {
  return screen.findByPlaceholderText("Ask anything about this PR…")
}

beforeEach(() => {
  vi.resetAllMocks()
  mocks.isLoading = false
  mocks.getReviewChat.mockResolvedValue({
    available: true,
    assistant_id: "assistant",
    thread_id: "thread",
  })
  mocks.approve.mockResolvedValue({ action: "approve", done: true })
})

afterEach(cleanup)

it("intercepts an exact trimmed /approve and reports success", async () => {
  let resolveApproval!: (value: { action: string; done: boolean }) => void
  mocks.approve.mockImplementation(
    () =>
      new Promise((resolve) => {
        resolveApproval = resolve
      })
  )
  renderChat()
  const input = await composerInput()
  fireEvent.change(input, { target: { value: "  /approve  " } })
  fireEvent.keyDown(input, { key: "Enter" })

  await waitFor(() =>
    expect(mocks.approve).toHaveBeenCalledExactlyOnceWith(
      "langchain-ai",
      "open-swe",
      42,
      "a".repeat(40)
    )
  )
  expect(mocks.submit).not.toHaveBeenCalled()
  expect((await screen.findByRole("status")).textContent).toContain(
    "Approving pull request…"
  )

  resolveApproval({ action: "approve", done: true })
  await waitFor(() =>
    expect(screen.getByRole("status").textContent).toContain(
      "Pull request approved."
    )
  )
})

it("sends non-exact approval text to the model", async () => {
  renderChat()
  const input = await composerInput()
  fireEvent.change(input, { target: { value: "/approve please" } })
  fireEvent.keyDown(input, { key: "Enter" })

  expect(mocks.approve).not.toHaveBeenCalled()
  expect(mocks.submit).toHaveBeenCalledExactlyOnceWith({
    messages: [{ type: "human", content: "/approve please" }],
  })
})

it("rejects /approve with an attachment and preserves the composer", async () => {
  renderChat({ withAttachmentButton: true })
  fireEvent.click(await screen.findByRole("button", { name: "Add attachment" }))
  const input = await composerInput()
  fireEvent.change(input, { target: { value: "/approve" } })
  fireEvent.click(screen.getByRole("button", { name: "Send message" }))

  expect((await screen.findByRole("alert")).textContent).toContain(
    "Remove attachments before approving the pull request."
  )
  expect((input as HTMLTextAreaElement).value).toBe("/approve")
  expect(screen.getByText("app.ts:R1")).not.toBeNull()
  expect(mocks.approve).not.toHaveBeenCalled()
  expect(mocks.submit).not.toHaveBeenCalled()
})

it("shows approval failures and does not submit duplicate pending commands", async () => {
  let rejectApproval!: (error: Error) => void
  mocks.approve.mockImplementation(
    () =>
      new Promise((_resolve, reject) => {
        rejectApproval = reject
      })
  )
  renderChat()
  const input = await composerInput()
  fireEvent.change(input, { target: { value: "/approve" } })
  fireEvent.keyDown(input, { key: "Enter" })
  fireEvent.keyDown(input, { key: "Enter" })
  await waitFor(() => expect(mocks.approve).toHaveBeenCalledTimes(1))

  rejectApproval(new Error("You cannot approve your own pull request"))
  expect((await screen.findByRole("alert")).textContent).toContain(
    "You cannot approve your own pull request"
  )
})

it("does not clear the composer when Enter is pressed while chat is busy", async () => {
  mocks.isLoading = true
  renderChat()
  const input = await composerInput()
  fireEvent.change(input, { target: { value: "Keep this draft" } })
  fireEvent.keyDown(input, { key: "Enter" })

  await waitFor(() =>
    expect((input as HTMLTextAreaElement).value).toBe("Keep this draft")
  )
  expect(mocks.submit).not.toHaveBeenCalled()
})
