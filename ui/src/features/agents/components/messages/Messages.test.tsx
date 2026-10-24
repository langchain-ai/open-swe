/** @vitest-environment jsdom */

import { cleanup, fireEvent, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

import {
  createMemoryHistory,
  createRootRoute,
  createRouter,
  RouterProvider,
} from "@tanstack/react-router"

import { AIMessage, HumanMessage, ToolMessage } from "@langchain/core/messages"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import type { ReactNode } from "react"

import { Messages } from "./Messages"
import { streamMessagesToUi } from "@/features/agents/lib/streamMessagesToUi"
import type { TaskEventMetadata } from "@/features/agents/lib/structuredInputMessages"

vi.stubGlobal(
  "ResizeObserver",
  class {
    observe() {}
    disconnect() {}
  }
)

vi.mock("@/features/agents/components/WorkflowApprovalCard", () => ({
  WorkflowApprovalCard: ({ threadId }: { threadId: string }) => (
    <div data-testid="workflow-approval-card">Approval for {threadId}</div>
  ),
}))

afterEach(() => cleanup())

const wrapper = ({ children }: { children: ReactNode }) => (
  <QueryClientProvider client={new QueryClient()}>
    {children}
  </QueryClientProvider>
)

describe("Messages", () => {
  it("renders attributed task activity with safe expandable details", async () => {
    const source = {
      version: 1,
      task_id: "d505b040-c025-4b52-a27e-339803281cfb",
      sender_thread_id: "86186b55-1999-52e2-bf4b-ca3de907043e",
      sender_role: "worker",
      sender_label: "Investigate login",
      content:
        'Can I change `login()`?\n> "Blocked" on <missing> & validation.\n```ts\nreturn value < 2\n```\n<img src=x onerror="alert(1)">',
    } as const
    const events: Array<TaskEventMetadata> = [
      { ...source, kind: "message", status: null },
      {
        ...source,
        sender_thread_id: "7db1bbf5-0623-5a35-a5a4-db372cfc31d4",
        sender_label: null,
        kind: "completion",
        status: "success",
      },
    ]
    const xml = (text: string) =>
      text
        .replaceAll("&", "&amp;")
        .replaceAll('"', "&quot;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
    const messages = streamMessagesToUi(
      events.map(
        (event, index) =>
          new HumanMessage({
            id: `task-event-${index}`,
            content: `<input-message sender="system:event-subscription" kind="system" surface="automation" event_match="11111111-1111-4111-8111-${String(index + 1).padStart(12, "0")}" task_event="${xml(JSON.stringify(event))}">\n${xml("Model-facing safety warning\n<untrusted-worker-output>hidden model text</untrusted-worker-output>")}\n</input-message>`,
          })
      )
    )
    const router = createRouter({
      routeTree: createRootRoute({
        component: () => <Messages messages={messages} isStreaming={false} />,
      }),
      history: createMemoryHistory({ initialEntries: ["/"] }),
    })
    await router.load()
    const { container } = render(<RouterProvider router={router} />)

    const details = screen.getByRole("button", {
      name: "Investigate login · Message",
    })
    expect(details.getAttribute("aria-expanded")).toBe("false")
    expect(
      screen
        .getByRole("link", { name: "Open Investigate login thread" })
        .getAttribute("href")
    ).toBe("/agents/86186b55-1999-52e2-bf4b-ca3de907043e")
    expect(
      screen
        .getByRole("link", { name: "Open Worker 2cfc31d4 thread" })
        .getAttribute("href")
    ).toBe("/agents/7db1bbf5-0623-5a35-a5a4-db372cfc31d4")
    expect(container.textContent).not.toContain("Can I change")
    fireEvent.click(details)
    expect(container.textContent).toContain(source.content)
    expect(container.querySelector("img,script")).toBeNull()
    expect(container.textContent).not.toContain("Model-facing safety warning")
    expect(container.textContent).not.toContain("untrusted-worker-output")
    expect(container.textContent).not.toContain("hidden model text")
    fireEvent.click(details)
    expect(container.textContent).not.toContain("Can I change")
  })

  it("keeps the worker launch in the conversation when surrounding work is folded", async () => {
    const workerId = "7db1bbf5-0623-5a35-a5a4-db372cfc31d4"
    const messages = streamMessagesToUi([
      new AIMessage({
        id: "spawn-turn",
        content: "",
        tool_calls: [
          { id: "read", name: "read_file", args: { path: "AGENTS.md" } },
          {
            id: "spawn",
            name: "start_async_task",
            args: {
              description: "Investigate <login> & validation",
              subagent_type: "general-purpose",
            },
          },
          { id: "later-read", name: "read_file", args: { path: "README.md" } },
        ],
      }),
      new ToolMessage({ tool_call_id: "read", content: "Instructions" }),
      new ToolMessage({
        tool_call_id: "spawn",
        content: JSON.stringify({ success: true, worker_thread_id: workerId }),
      }),
      new ToolMessage({ tool_call_id: "later-read", content: "Docs" }),
      new AIMessage({
        id: "reply",
        content: "The worker is continuing independently.",
      }),
    ])
    const router = createRouter({
      routeTree: createRootRoute({
        component: () => <Messages messages={messages} isStreaming={false} />,
      }),
      history: createMemoryHistory({ initialEntries: ["/"] }),
    })
    await router.load()
    const { container } = render(<RouterProvider router={router} />)
    const chip = screen.getByRole("button", {
      name: "Worker 2cfc31d4 · Started",
    })
    const folds = screen.getAllByRole("button", { name: "Worked · 1 action" })
    expect(folds).toHaveLength(2)
    for (const fold of folds) {
      expect(fold.getAttribute("aria-expanded")).toBe("false")
    }
    expect(
      folds[0]!.compareDocumentPosition(chip) & Node.DOCUMENT_POSITION_FOLLOWING
    ).toBeTruthy()
    expect(
      chip.compareDocumentPosition(folds[1]!) & Node.DOCUMENT_POSITION_FOLLOWING
    ).toBeTruthy()
    expect(
      screen
        .getByRole("link", { name: "Open Worker 2cfc31d4 thread" })
        .getAttribute("href")
    ).toBe(`/agents/${workerId}`)
    expect(
      chip.compareDocumentPosition(
        screen.getByText("The worker is continuing independently.")
      ) & Node.DOCUMENT_POSITION_FOLLOWING
    ).toBeTruthy()
    expect(screen.queryByText("Investigate <login> & validation")).toBeNull()
    fireEvent.click(chip)
    expect(screen.getByText("Investigate <login> & validation")).toBeTruthy()
    expect(container.querySelector("login")).toBeNull()
  })

  it.each([
    ["in_progress", undefined, "Starting…"],
    ["error", "launch failed", "Launch failed"],
    ["completed", '{"success":false}', "Launch failed"],
    [
      "completed",
      '{"success":true,"worker_thread_id":"invalid"}',
      "Launch status unknown",
    ],
  ] as const)(
    "does not claim a worker started for %s / %s",
    (status, output, label) => {
      render(
        <Messages
          isStreaming={false}
          messages={[
            {
              id: "spawn",
              author: "agent",
              timestamp: "2026-09-03T10:30:00Z",
              chunks: [
                {
                  kind: "tool-execution",
                  toolCallId: "spawn",
                  toolName: "start_async_task",
                  toolKind: "other",
                  title: "Start async task",
                  status,
                  output,
                },
              ],
            },
          ]}
        />
      )
      expect(
        screen.getByRole("button", { name: `Worker · ${label}` })
      ).toBeTruthy()
      expect(screen.queryByRole("link")).toBeNull()
    }
  )

  it("expands system context with the shared chip", () => {
    render(
      <Messages
        isStreaming={false}
        messages={[
          {
            id: "system-context",
            author: "user",
            timestamp: "2026-09-03T10:30:00.000Z",
            structuredSenderKind: "system",
            structuredSenderName: "Scheduler",
            chunks: [{ kind: "text", text: "Check the latest deployment" }],
          },
        ]}
      />
    )
    const toggle = screen.getByRole("button", { name: "Scheduler" })
    expect(screen.queryByText("Check the latest deployment")).toBeNull()
    fireEvent.click(toggle)
    expect(toggle.getAttribute("aria-expanded")).toBe("true")
    expect(screen.getByText("Check the latest deployment")).toBeTruthy()
    fireEvent.click(toggle)
    expect(screen.queryByText("Check the latest deployment")).toBeNull()
  })

  it("shows run activity while a stream is starting with no messages", () => {
    render(<Messages messages={[]} isStreaming />)

    expect(screen.getByRole("status").textContent).toBe("Working…0s")
  })

  it("shows reconnect activity in the existing status line", () => {
    render(
      <Messages
        messages={[]}
        isStreaming
        reconnectLabel="Reconnecting… 3/12 (retrying in 4s)"
      />
    )

    expect(screen.getByRole("status").textContent).toBe(
      "Reconnecting… 3/12 (retrying in 4s)0s"
    )
  })

  it("renders a sent Slack reply before the work that follows it", () => {
    render(
      <Messages
        isStreaming
        messages={[
          {
            id: "agent-turn",
            author: "agent",
            timestamp: "2026-09-03T10:30:00.000Z",
            chunks: [
              {
                kind: "tool-execution",
                toolCallId: "reply",
                title: "Slack thread reply",
                toolKind: "slack",
                status: "completed",
                input: { message: "On it!" },
              },
              {
                kind: "tool-execution",
                toolCallId: "shell",
                title: "sleep 20",
                toolKind: "execute",
                status: "in_progress",
              },
            ],
          },
        ]}
      />,
      { wrapper }
    )

    const reply = screen.getByText("On it!")
    const work = screen.getByRole("button", {
      name: "Running… · 1 action",
    })
    expect(
      reply.compareDocumentPosition(work) & Node.DOCUMENT_POSITION_FOLLOWING
    ).toBeTruthy()
  })

  it("folds each stretch of work between the Slack replies around it", () => {
    render(
      <Messages
        isStreaming
        messages={[
          {
            id: "agent-turn",
            author: "agent",
            timestamp: "2026-09-03T10:30:00.000Z",
            chunks: [
              {
                kind: "tool-execution",
                toolCallId: "ack",
                title: "Slack thread reply",
                toolKind: "slack",
                status: "completed",
                input: { message: "On it!" },
              },
              {
                kind: "tool-execution",
                toolCallId: "shell",
                title: "ls",
                toolKind: "execute",
                status: "completed",
              },
              {
                kind: "tool-execution",
                toolCallId: "progress",
                title: "Slack thread reply",
                toolKind: "slack",
                status: "completed",
                input: { message: "Found the cause" },
              },
            ],
          },
        ]}
      />,
      { wrapper }
    )

    const inOrder = [
      screen.getByText("On it!"),
      screen.getByRole("button", { name: "Worked · 1 action" }),
      screen.getByText("Found the cause"),
      screen.getByRole("button", { name: "Thinking…" }),
    ]
    inOrder.slice(1).forEach((later, index) => {
      expect(
        inOrder[index]!.compareDocumentPosition(later) &
          Node.DOCUMENT_POSITION_FOLLOWING
      ).toBeTruthy()
    })
  })

  it("keeps the agent's commentary between folds and copies only its answer", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText },
    })
    Object.defineProperty(window, "isSecureContext", {
      configurable: true,
      value: true,
    })
    const shell = (toolCallId: string, timestamp: string) => ({
      kind: "tool-execution" as const,
      toolCallId,
      timestamp,
      title: "ls",
      toolKind: "execute" as const,
      status: "completed" as const,
    })
    render(
      <Messages
        isStreaming={false}
        messages={[
          {
            id: "agent-turn",
            author: "agent",
            startedAt: "2026-09-03T10:00:00.000Z",
            timestamp: "2026-09-03T10:10:00.000Z",
            chunks: [
              {
                kind: "text",
                text: "Checking the tests first.",
                timestamp: "2026-09-03T10:00:00.000Z",
              },
              shell("first", "2026-09-03T10:00:05.000Z"),
              {
                kind: "text",
                text: "Found the cause.",
                timestamp: "2026-09-03T10:02:00.000Z",
              },
              shell("second", "2026-09-03T10:02:05.000Z"),
              {
                kind: "text",
                text: "Fixed it.",
                timestamp: "2026-09-03T10:10:00.000Z",
              },
            ],
          },
        ]}
      />,
      { wrapper }
    )

    const inOrder = [
      screen.getByText("Checking the tests first."),
      screen.getByRole("button", { name: "Worked for 2m 0s · 1 action" }),
      screen.getByText("Found the cause."),
      screen.getByRole("button", { name: "Worked for 8m 0s · 1 action" }),
      screen.getByText("Fixed it."),
    ]
    inOrder.slice(1).forEach((later, index) => {
      expect(
        inOrder[index]!.compareDocumentPosition(later) &
          Node.DOCUMENT_POSITION_FOLLOWING
      ).toBeTruthy()
    })

    fireEvent.click(screen.getByRole("button", { name: "Copy message" }))
    await vi.waitFor(() => expect(writeText).toHaveBeenCalledWith("Fixed it."))
  })

  it("renders unfinished grouped work after its fold row", () => {
    render(
      <Messages
        isStreaming={false}
        messages={[
          {
            id: "agent-turn",
            author: "agent",
            timestamp: "2026-09-03T10:30:00.000Z",
            chunks: [
              {
                kind: "tool-execution",
                toolCallId: "reply",
                title: "Slack thread reply",
                toolKind: "slack",
                status: "completed",
                input: { message: "On it!" },
              },
              {
                kind: "tool-execution",
                toolCallId: "task",
                title: "Task",
                toolKind: "task",
                status: "in_progress",
              },
            ],
          },
        ]}
      />,
      { wrapper }
    )

    const reply = screen.getByText("On it!")
    const fold = screen.getByRole("button", { name: "Worked · 1 action" })
    const groupedWork = screen.getByText("Task")

    expect(
      reply.compareDocumentPosition(fold) & Node.DOCUMENT_POSITION_FOLLOWING
    ).toBeTruthy()
    expect(
      fold.compareDocumentPosition(groupedWork) &
        Node.DOCUMENT_POSITION_FOLLOWING
    ).toBeTruthy()

    fireEvent.click(fold)
    expect(screen.getByText("subagent")).toBeTruthy()
  })

  it("keeps workflow approval available alongside an empty-state error", () => {
    render(
      <Messages
        messages={[]}
        threadId="thread-1"
        emptyState={<div>Messages could not be loaded</div>}
        isStreaming={false}
      />
    )

    expect(screen.getByText("Messages could not be loaded")).toBeTruthy()
    expect(screen.getByTestId("workflow-approval-card").textContent).toBe(
      "Approval for thread-1"
    )
  })
})
