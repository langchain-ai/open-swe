/** @vitest-environment jsdom */

import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import { ChatComposer, buildCommandItems } from "./ChatComposer"
import { ComposerPrimaryActions } from "./ComposerPrimaryActions"
import { replaceTextRange } from "./composerTrigger"
import type { ChatComposerProps } from "./ChatComposer"
import { AgentThreadStreamBoundary } from "@/features/agents/lib/provider/useIsInAgentThreadStream"
import { ThreadSourceProvider } from "@/features/agents/lib/threadSource/ThreadSourceProvider"

const stream = {
  isLoading: false,
  threadId: "thread-1",
  messages: [],
  toolCalls: [],
  isThreadLoading: false,
  hydrationPromise: Promise.resolve(),
  error: null,
  submit: vi.fn(),
  disconnect: vi.fn(),
  getThread: () => null,
}

vi.mock("@langchain/react", () => ({
  useStream: () => stream,
  useChannelEffect: () => {},
  useSubmissionQueue: () => ({
    entries: [],
    size: 0,
    cancel: vi.fn(),
    clear: vi.fn(),
  }),
}))

vi.mock("@/lib/langgraph-client", () => ({
  createDashboardClient: () => ({}),
  createLocalGraphClient: () => ({}),
  dashboardFetch: fetch,
}))

const cancelThread = vi.fn((threadId: string) =>
  Promise.resolve({ id: threadId, status: "interrupted" })
)

vi.mock("@/features/agents/lib/api", () => ({
  agentsApi: { cancelThread: (threadId: string) => cancelThread(threadId) },
  AgentsApiError: class AgentsApiError extends Error {},
}))

vi.mock("@/lib/appCommands", () => ({
  useRegisterAppCommands: vi.fn(),
}))

afterEach(() => cleanup())

beforeEach(() => {
  stream.isLoading = false
  stream.disconnect.mockClear()
  cancelThread.mockClear()
})

function renderComposer(
  running: boolean,
  props: Partial<ChatComposerProps> = {}
) {
  const client = new QueryClient({
    defaultOptions: { mutations: { retry: false }, queries: { retry: false } },
  })
  render(
    <QueryClientProvider client={client}>
      <AgentThreadStreamBoundary>
        <ThreadSourceProvider threadId="thread-1" transcript={false}>
          <ChatComposer
            activeRun={{ threadId: "thread-1", running }}
            {...props}
          />
        </ThreadSourceProvider>
      </AgentThreadStreamBoundary>
    </QueryClientProvider>
  )
}

describe("ChatComposer stop button", () => {
  it("offers to stop a run this client never joined", async () => {
    renderComposer(true)

    fireEvent.click(screen.getByRole("button", { name: "Stop run" }))

    await waitFor(() => expect(cancelThread).toHaveBeenCalledWith("thread-1"))
    expect(stream.disconnect).toHaveBeenCalled()
  })

  it("cancels server-side even while streaming, since stop() may know no run id", async () => {
    stream.isLoading = true
    renderComposer(false)

    fireEvent.click(screen.getByRole("button", { name: "Stop run" }))

    await waitFor(() => expect(cancelThread).toHaveBeenCalledWith("thread-1"))
  })

  it("keeps the run live when cancellation fails", async () => {
    cancelThread.mockRejectedValueOnce(new Error("502"))
    renderComposer(true)

    fireEvent.click(screen.getByRole("button", { name: "Stop run" }))

    await waitFor(() => expect(cancelThread).toHaveBeenCalled())
    // No false "stopped" state: the stream stays connected so status polling
    // (which only runs while the cached status is `running`) keeps going.
    expect(stream.disconnect).not.toHaveBeenCalled()
    expect(screen.getByRole("button", { name: "Stop run" })).toBeTruthy()
  })

  it("stops the run on Escape", async () => {
    renderComposer(true)

    fireEvent.keyDown(document.body, { key: "Escape" })

    await waitFor(() => expect(cancelThread).toHaveBeenCalledWith("thread-1"))
  })

  it("leaves Escape to an open overlay", () => {
    renderComposer(true)
    const dialog = document.createElement("div")
    dialog.setAttribute("role", "dialog")
    document.body.appendChild(dialog)

    fireEvent.keyDown(dialog, { key: "Escape" })

    expect(cancelThread).not.toHaveBeenCalled()
  })

  it("ignores Escape when no run is live", () => {
    renderComposer(false)

    fireEvent.keyDown(document.body, { key: "Escape" })

    expect(cancelThread).not.toHaveBeenCalled()
  })

  it("shows only the stop action while a live run has no queued message", () => {
    renderComposer(true)

    expect(screen.getByRole("button", { name: "Stop run" })).toBeTruthy()
    expect(screen.queryByRole("button", { name: "Queue message" })).toBeNull()
  })

  it("shows the send button when no run is live", () => {
    renderComposer(false)

    expect(screen.getByRole("button", { name: "Send message" })).toBeTruthy()
    expect(screen.queryByRole("button", { name: "Queue message" })).toBeNull()
    expect(screen.queryByRole("button", { name: "Stop run" })).toBeNull()
  })

  it("shows stop while a run submission is dispatching", () => {
    render(
      <ComposerPrimaryActions
        canSubmit={false}
        onStop={vi.fn()}
        onSubmit={vi.fn()}
        submitting
      />
    )

    expect(screen.getByRole("button", { name: "Stop run" })).toBeTruthy()
  })

  it("stops a direct run on Escape while steer is shown", () => {
    const onStop = vi.fn()
    render(
      <ComposerPrimaryActions
        activeRun={{ threadId: "thread-1", running: true }}
        canSubmit
        onStop={onStop}
        onSubmit={vi.fn()}
        submitting={false}
      />
    )

    expect(screen.getByRole("button", { name: "Queue message" })).toBeTruthy()
    fireEvent.keyDown(document.body, { key: "Escape" })

    expect(onStop).toHaveBeenCalledOnce()
  })
})

describe("ChatComposer options", () => {
  it("offers attachments without a plan-mode toggle", async () => {
    renderComposer(false)

    fireEvent.click(
      screen.getByRole("button", { name: "More composer options" })
    )

    expect(
      await screen.findByRole("menuitem", { name: "Attach images" })
    ).toBeTruthy()
    expect(screen.queryByRole("menuitem", { name: /plan mode/i })).toBeNull()
  })
})

describe("ChatComposer skill autocomplete", () => {
  it("omits the model command when no model picker is available", () => {
    const items = buildCommandItems(
      {
        kind: "slash-command",
        query: "model",
        rangeStart: 0,
        rangeEnd: 6,
      },
      [],
      [],
      false
    )

    expect(items).toEqual([])
  })

  it("shows only skills for the dollar picker while slash keeps both", () => {
    const skills = [
      {
        name: "baby-sit",
        description: "Monitor a pull request",
        instructions: "",
      },
    ]
    const dollarItems = buildCommandItems(
      {
        kind: "skill-command",
        query: "baby",
        rangeStart: 0,
        rangeEnd: 5,
      },
      [],
      skills
    )
    const slashItems = buildCommandItems(
      {
        kind: "slash-command",
        query: "",
        rangeStart: 0,
        rangeEnd: 1,
      },
      [],
      skills
    )

    expect(dollarItems).toEqual([
      expect.objectContaining({
        type: "skill",
        name: "baby-sit",
        label: "/baby-sit",
      }),
    ])
    expect(slashItems.some((item) => item.type === "slash-command")).toBe(true)
    expect(slashItems.some((item) => item.type === "skill")).toBe(true)
  })

  it("prefers a colliding skill and preserves surrounding prompt text", () => {
    const trigger = {
      kind: "slash-command" as const,
      query: "model",
      rangeStart: 7,
      rangeEnd: 13,
    }
    const items = buildCommandItems(
      trigger,
      [],
      [
        {
          name: "model",
          description: "Inspect a model",
          instructions: "",
        },
      ]
    )

    expect(items).toEqual([
      expect.objectContaining({ type: "skill", name: "model" }),
    ])
    expect(replaceTextRange("Please /model this", 7, 13, "/model ").text).toBe(
      "Please /model  this"
    )
  })
})
