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

const stream = {
  isLoading: false,
  threadId: "thread-1",
  stop: vi.fn(),
  disconnect: vi.fn(),
}

vi.mock("@langchain/react", () => ({
  useStreamContext: () => stream,
}))

const cancelThread = vi.fn(async (threadId: string) => ({
  id: threadId,
  status: "interrupted",
}))

vi.mock("@/features/agents/lib/api", () => ({
  agentsApi: { cancelThread: (threadId: string) => cancelThread(threadId) },
}))

vi.mock("@/lib/appCommands", () => ({
  useRegisterAppCommands: vi.fn(),
}))

afterEach(() => cleanup())

beforeEach(() => {
  stream.isLoading = false
  stream.stop.mockClear()
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
        <ChatComposer
          activeRun={{ threadId: "thread-1", running }}
          {...props}
        />
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
    expect(screen.queryByRole("button", { name: "Steer agent" })).toBeNull()
  })

  it("shows the send button when no run is live", () => {
    renderComposer(false)

    expect(screen.getByRole("button", { name: "Send message" })).toBeTruthy()
    expect(screen.queryByRole("button", { name: "Steer agent" })).toBeNull()
    expect(screen.queryByRole("button", { name: "Stop run" })).toBeNull()
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

    expect(screen.getByRole("button", { name: "Steer agent" })).toBeTruthy()
    fireEvent.keyDown(document.body, { key: "Escape" })

    expect(onStop).toHaveBeenCalledOnce()
  })
})

describe("ChatComposer admin mode", () => {
  it("hides the toggle when admin mode is unavailable", () => {
    renderComposer(false)

    expect(screen.queryByRole("button", { name: "Admin mode" })).toBeNull()
  })

  it("shows a direct toggle for eligible admins", () => {
    const onAdminThreadChange = vi.fn()
    renderComposer(false, { onAdminThreadChange })

    const toggle = screen.getByRole("button", { name: "Admin mode" })
    expect(toggle.getAttribute("aria-pressed")).toBe("false")

    fireEvent.click(toggle)

    expect(onAdminThreadChange).toHaveBeenCalledWith(true)
  })

  it("shows the active state and can disable admin mode", () => {
    const onAdminThreadChange = vi.fn()
    renderComposer(false, {
      adminThread: true,
      onAdminThreadChange,
    })

    const toggle = screen.getByRole("button", { name: "Admin mode" })
    expect(toggle.getAttribute("aria-pressed")).toBe("true")
    expect(toggle.className.split(" ")).toContain("bg-destructive/10")

    fireEvent.click(toggle)

    expect(onAdminThreadChange).toHaveBeenCalledWith(false)
  })

  it("cannot change modes while the composer is disabled", () => {
    const onAdminThreadChange = vi.fn()
    renderComposer(false, { disabled: true, onAdminThreadChange })

    const toggle = screen.getByRole("button", { name: "Admin mode" })
    expect(toggle.hasAttribute("disabled")).toBe(true)

    fireEvent.click(toggle)

    expect(onAdminThreadChange).not.toHaveBeenCalled()
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
      query: "plan",
      rangeStart: 7,
      rangeEnd: 12,
    }
    const items = buildCommandItems(
      trigger,
      [],
      [
        {
          name: "plan",
          description: "Create an implementation plan",
          instructions: "",
        },
      ]
    )

    expect(items).toEqual([
      expect.objectContaining({ type: "skill", name: "plan" }),
    ])
    expect(replaceTextRange("Please /plan this", 7, 12, "/plan ").text).toBe(
      "Please /plan  this"
    )
  })
})
