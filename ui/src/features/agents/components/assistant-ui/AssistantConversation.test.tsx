/** @vitest-environment jsdom */

import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import type { AppendMessage } from "@assistant-ui/react"
import AssistantConversation, {
  appendMessageInput,
} from "./AssistantConversation"
import type { Message, ToolExecutionChunk } from "@/features/agents/lib/types"

vi.mock("@/features/agents/components/chat/Markdown", () => ({
  Markdown: ({ content }: { content: string }) => <div>{content}</div>,
}))
vi.mock("@/features/agents/components/InlinePlanArtifact", () => ({
  InlinePlanArtifact: ({ threadId }: { threadId: string }) => (
    <div>Plan for {threadId}</div>
  ),
}))
const user: Message = {
  id: "user-1",
  author: "user",
  timestamp: "2026-09-12T08:00:00Z",
  structuredSenderName: "Alice",
  structuredSurface: "slack",
  chunks: [
    { kind: "text", text: "Inspect the failing build" },
    {
      kind: "image",
      base64: "aGVsbG8=",
      mimeType: "image/png",
      fileName: "build.png",
    },
  ],
}
const tool: ToolExecutionChunk = {
  kind: "tool-execution",
  toolCallId: "tool-1",
  title: "execute",
  toolKind: "execute",
  input: { command: "pnpm build" },
  status: "completed",
  output: "Build complete",
}
const agent: Message = {
  id: "agent-1",
  author: "agent",
  timestamp: "2026-09-12T08:01:00Z",
  chunks: [tool, { kind: "text", text: "The build is fixed." }],
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
})
afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
  Reflect.deleteProperty(HTMLElement.prototype, "scrollTo")
})

function fill(text: string) {
  fireEvent.change(screen.getByRole("textbox", { name: "Message input" }), {
    target: { value: text },
  })
}

describe("assistant-ui conversation", () => {
  it("renders messages, tool details, attachments, queued messages, and plans with native message parts", () => {
    render(
      <AssistantConversation
        composer={{}}
        threadId="thread-1"
        messages={[
          user,
          {
            ...user,
            id: "hidden",
            hidden: true,
            chunks: [{ kind: "text", text: "Hidden context" }],
          },
          agent,
        ]}
        isStreaming={false}
        showPlanArtifact
        queuedMessages={[{ id: "q", content: "Check lint", createdAt: 1 }]}
      />
    )
    expect(screen.getByText("Alice · Slack")).toBeTruthy()
    expect(
      screen.getByRole("img", { name: "build.png" }).getAttribute("src")
    ).toBe("data:image/png;base64,aGVsbG8=")
    expect(screen.getByText("The build is fixed.")).toBeTruthy()
    expect(screen.getByText("Check lint")).toBeTruthy()
    expect(screen.getByText("Plan for thread-1")).toBeTruthy()
    expect(screen.queryByText("Hidden context")).toBeNull()
    fireEvent.click(screen.getByText("Show activity"))
    fireEvent.click(screen.getByText("execute"))
    expect(screen.getByText("Build complete")).toBeTruthy()
  })
  it("groups calls across a turn while keeping approvals and streamed failures outside the collapsed activity", () => {
    const approval = {
      ...tool,
      toolCallId: "approval",
      title: "Approve changes",
      approvalRequestId: "approval-1",
    }
    const pending = { ...tool, status: "in_progress" as const }
    const second = { ...tool, toolCallId: "read", title: "Read file" }
    const messages = (first: ToolExecutionChunk) => [{
      ...agent,
      chunks: [first, { kind: "text" as const, text: "Checking files." }, second, approval],
    }]
    const view = render(
      <AssistantConversation composer={{}} messages={messages(pending)} isStreaming />
    )
    const activity = screen.getByText("Show activity").closest("details")!
    expect(screen.getAllByText("Show activity")).toHaveLength(1)
    expect(activity.open).toBe(false)
    expect(activity.contains(screen.getByText("execute"))).toBe(true)
    expect(activity.contains(screen.getByText("Read file"))).toBe(true)
    expect(activity.contains(screen.getByText("Checking files."))).toBe(false)
    expect(activity.contains(screen.getByText("Approve changes"))).toBe(false)
    expect(screen.getByText("Approve changes").closest("details")!.open).toBe(true)
    expect(activity.querySelector("summary")!.textContent).toContain("2 calls · Running")

    fireEvent.click(screen.getByText("Show activity"))
    expect(activity.open).toBe(true)
    view.rerender(
      <AssistantConversation
        composer={{}}
        messages={messages({ ...pending, status: "error", output: "Build failed" })}
        isStreaming={false}
      />
    )
    expect(screen.getByText("Show activity").closest("details")).toBe(activity)
    expect(activity.open).toBe(true)
    expect(activity.contains(screen.getByText("execute"))).toBe(false)
    expect(screen.getByText("Failed")).toBeTruthy()
    expect(activity.querySelector("summary")!.textContent).toContain("1 call")
    fireEvent.click(screen.getByText("Hide activity"))
    expect(activity.open).toBe(false)
    fireEvent.click(screen.getByText("execute"))
    expect(screen.getByText("Build failed").closest("details")!.open).toBe(true)
  })
  it("sends through the host callback and allows immediate follow-ups while running", async () => {
    const send = vi.fn().mockResolvedValue(undefined)
    const stop = vi.fn().mockResolvedValue(undefined)
    const view = render(
      <AssistantConversation
        composer={{ onSubmit: send, onStop: stop }}
        messages={[user]}
        isStreaming={false}
      />
    )
    fill("First message")
    fireEvent.click(screen.getByRole("button", { name: "Send message" }))
    await waitFor(() => expect(send).toHaveBeenCalledWith("First message", []))
    view.rerender(
      <AssistantConversation
        composer={{ onSubmit: send, onStop: stop }}
        messages={[user]}
        isStreaming
      />
    )
    await waitFor(() =>
      expect(
        (screen.getByRole("textbox") as HTMLTextAreaElement).disabled
      ).toBe(false)
    )
    fill("Follow up now")
    fireEvent.click(screen.getByRole("button", { name: "Send follow up" }))
    await waitFor(() => expect(send).toHaveBeenCalledWith("Follow up now", []))
    expect(send).toHaveBeenCalledTimes(2)
    await waitFor(() =>
      expect(
        (screen.getByRole("button", { name: "Stop run" }) as HTMLButtonElement)
          .disabled
      ).toBe(false)
    )
    fireEvent.click(screen.getByRole("button", { name: "Stop run" }))
    await waitFor(() => expect(stop).toHaveBeenCalledOnce())
  })
  it("restores a failed send and does not accept input in an admin-only conversation", async () => {
    const send = vi.fn().mockRejectedValue(new Error("Connection lost"))
    const view = render(
      <AssistantConversation
        composer={{ onSubmit: send }}
        messages={[]}
        isStreaming={false}
      />
    )
    fill("Keep this draft")
    fireEvent.click(screen.getByRole("button", { name: "Send message" }))
    expect(await screen.findByRole("alert")).toHaveProperty(
      "textContent",
      "Connection lost"
    )
    await waitFor(() =>
      expect((screen.getByRole("textbox") as HTMLTextAreaElement).value).toBe(
        "Keep this draft"
      )
    )
    view.rerender(
      <AssistantConversation
        composer={{ onSubmit: send, disabled: true }}
        messages={[]}
        isStreaming={false}
      />
    )
    await waitFor(() =>
      expect(
        (screen.getByRole("textbox") as HTMLTextAreaElement).disabled
      ).toBe(true)
    )
    fireEvent.click(screen.getByRole("button", { name: "Send message" }))
    expect(send).toHaveBeenCalledTimes(1)
  })
  it("updates a streaming message without duplicating its text", async () => {
    const view = render(
      <AssistantConversation
        composer={{}}
        messages={[
          user,
          { ...agent, chunks: [{ kind: "text", text: "Checking" }] },
        ]}
        isStreaming
      />
    )
    expect(screen.getByText("Checking")).toBeTruthy()
    view.rerender(
      <AssistantConversation
        composer={{}}
        messages={[user, agent]}
        isStreaming={false}
      />
    )
    expect(await screen.findByText("The build is fixed.")).toBeTruthy()
    expect(screen.queryByText("Checking")).toBeNull()
  })
  it("preserves image payloads and names at the backend boundary", () => {
    const message: AppendMessage = {
      role: "user",
      createdAt: new Date(),
      parentId: null,
      sourceId: null,
      runConfig: {},
      metadata: { custom: {} },
      content: [{ type: "text", text: "Look at this" }],
      attachments: [
        {
          id: "image-1",
          type: "image",
          status: { type: "complete" },
          name: "build.png",
          content: [{ type: "image", image: "data:image/png;base64,aGVsbG8=" }],
        },
      ],
    }
    expect(appendMessageInput(message)).toEqual({
      content: "Look at this",
      images: [
        {
          kind: "image",
          mimeType: "image/png",
          base64: "aGVsbG8=",
          fileName: "build.png",
        },
      ],
    })
    expect(() =>
      appendMessageInput({
        ...message,
        attachments: Array.from({ length: 6 }, () => message.attachments![0]!),
      })
    ).toThrow("up to 5")
  })
})
