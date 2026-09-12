/** @vitest-environment jsdom */

import { act, cleanup, fireEvent, render, screen } from "@testing-library/react"
import type { BaseMessage } from "@langchain/core/messages"
import { AIMessage, HumanMessage } from "@langchain/core/messages"
import { afterEach, beforeEach, expect, it, vi } from "vitest"

import { ConversationTestHarness as AssistantConversation } from "./ConversationTestHarness"
import { useMessages, useToolCalls } from "@langchain/react"
import type { Message, ToolExecutionChunk } from "@/features/agents/lib/types"
import { AgentThreadStreamBoundary } from "@/features/agents/lib/provider/useIsInAgentThreadStream"

const { stream, state } = vi.hoisted(() => ({
  state: { messages: [] as BaseMessage[], listeners: new Set<() => void>() },
  stream: { subagents: new Map([["task-1", { namespace: ["tools:task-1"] }]]) },
}))
vi.mock("@/features/agents/lib/stream/AgentStreamProvider", () => ({
  useAgentStream: () => stream,
}))
vi.mock("@langchain/react", async () => {
  const { useSyncExternalStore } = await import("react")
  return {
    useMessages: vi.fn(() =>
      useSyncExternalStore(
        (listener) => {
          state.listeners.add(listener)
          return () => {
            state.listeners.delete(listener)
          }
        },
        () => state.messages,
        () => state.messages
      )
    ),
    useToolCalls: vi.fn(),
  }
})
vi.mock("@/features/agents/components/chat/Markdown", () => ({
  Markdown: ({ content }: { content: string }) => <div>{content}</div>,
}))

const task: ToolExecutionChunk = {
  kind: "tool-execution",
  toolKind: "task",
  toolCallId: "task-1",
  title: "task",
  status: "in_progress",
  input: {
    subagent_type: "researcher",
    description: "Inspect stream ownership",
  },
}
const parent: Message = {
  id: "parent",
  author: "agent",
  timestamp: "2026-09-12T08:00:00Z",
  chunks: [task],
}

beforeEach(() => {
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  )
  vi.stubGlobal(
    "IntersectionObserver",
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  )
  Object.defineProperty(HTMLElement.prototype, "scrollTo", {
    configurable: true,
    value: vi.fn(),
  })
  state.messages = [
    new HumanMessage({ id: "nested-user", content: "Read the stream adapter" }),
    new AIMessage({
      id: "nested-agent",
      content: "The parent owns the stream.",
    }),
  ]
  vi.mocked(useToolCalls).mockReturnValue([])
})
afterEach(() => {
  cleanup()
  vi.clearAllMocks()
  vi.unstubAllGlobals()
  Reflect.deleteProperty(HTMLElement.prototype, "scrollTo")
})

it("opens the discovered namespace as a native nested conversation and updates its messages", async () => {
  render(
    <AgentThreadStreamBoundary>
      <AssistantConversation composer={{}} messages={[parent]} isStreaming />
    </AgentThreadStreamBoundary>
  )
  expect(useMessages).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole("button", { name: /researcher.*Working/ }))
  expect(useMessages).toHaveBeenCalledWith(stream, {
    namespace: ["tools:task-1"],
  })
  expect(useToolCalls).toHaveBeenCalledWith(stream, {
    namespace: ["tools:task-1"],
  })
  expect(await screen.findByText("The parent owns the stream.")).toBeTruthy()
  act(() => {
    state.messages = [
      new AIMessage({
        id: "nested-agent",
        content: "The subscription closes when collapsed.",
      }),
    ]
    state.listeners.forEach((listener) => listener())
  })
  expect(
    await screen.findByText("The subscription closes when collapsed.")
  ).toBeTruthy()
  fireEvent.click(screen.getByRole("button", { name: /researcher.*Working/ }))
  expect(
    screen.queryByText("The subscription closes when collapsed.")
  ).toBeNull()
  expect(state.listeners.size).toBe(0)
})

it("shows stored output and failures without requiring an active stream", () => {
  render(
    <AssistantConversation
      composer={{}}
      messages={[
        {
          ...parent,
          chunks: [
            { ...task, status: "error", output: "Repository unavailable" },
          ],
        },
      ]}
      isStreaming={false}
    />
  )
  fireEvent.click(screen.getByRole("button", { name: /researcher.*Failed/ }))
  expect(screen.getByText("Repository unavailable")).toBeTruthy()
  expect(useMessages).not.toHaveBeenCalled()
})
