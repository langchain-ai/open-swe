/** @vitest-environment jsdom */

import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react"
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
import { BackgroundEvent } from "@/features/agents/lib/backgroundEvent"
import { streamMessagesToUi } from "@/features/agents/lib/streamMessagesToUi"
import { attachmentUrl } from "@/features/agents/lib/transcript/api"
import type { TaskEventMetadata } from "@/features/agents/lib/structuredInputMessages"
import type { Message, ToolExecutionChunk } from "@/features/agents/lib/types"

vi.mock("@/lib/appCommands", () => ({
  useAppCommand: () => undefined,
  useRegisterAppCommands: () => {},
}))

vi.stubGlobal(
  "ResizeObserver",
  class {
    observe() {}
    disconnect() {}
  }
)

vi.mock("@/features/agents/components/WorkflowApprovalCard", () => ({
  WorkflowApprovalCard: ({
    threadId,
    latestUserMessageAt,
  }: {
    threadId: string
    latestUserMessageAt?: string
  }) => (
    <div
      data-testid="workflow-approval-card"
      data-latest-user-message-at={latestUserMessageAt}
    >
      Approval for {threadId}
    </div>
  ),
}))

afterEach(() => {
  cleanup()
  vi.unstubAllEnvs()
})

const wrapper = ({ children }: { children: ReactNode }) => (
  <QueryClientProvider client={new QueryClient()}>
    {children}
  </QueryClientProvider>
)

describe("Messages", () => {
  it("hides system message pills while preserving human messages", () => {
    const messages = streamMessagesToUi([
      new HumanMessage({
        id: "listener",
        content:
          '<dynamic-context kind="system" id="system:event-subscription">\ndisplay_name: Event listener\n</dynamic-context>',
      }),
      ...["Build completed", "Review posted", "Checks passed"].map(
        (text, index) =>
          new HumanMessage({
            id: `event-${index}`,
            content: `<input-message sender="system:event-subscription" kind="system" surface="github">${text}</input-message>`,
          })
      ),
      new HumanMessage({
        id: "pull-request-notice",
        content:
          '<input-message sender="system:pull-request-thread" kind="system" surface="automation">Pull request context</input-message>',
      }),
      ...[
        [
          "system:sandbox-handoff",
          "Moved to the cloud. Continuing from the branch as pushed.",
        ],
        ["system:api_key/key-1", "Investigate the nightly job failure"],
        ["system:github_actions/org/repo", "Fix the deployment check"],
      ].map(
        ([sender, text]) =>
          new HumanMessage({
            id: sender,
            content: `<input-message sender="${sender}" kind="system" surface="automation">${text}</input-message>`,
          })
      ),
      new HumanMessage({ id: "human", content: "Please continue" }),
      new HumanMessage({
        id: "separate-event",
        content:
          '<input-message sender="system:event-subscription" kind="system" surface="github">New commit</input-message>',
      }),
    ])
    render(<Messages messages={messages} isStreaming={false} />, { wrapper })

    expect(screen.getByText("Please continue")).toBeTruthy()
    expect(screen.queryByRole("button", { name: /event listener/i })).toBeNull()
    expect(screen.queryByText("Build completed")).toBeNull()
    expect(screen.queryByText("New commit")).toBeNull()
    expect(screen.queryByRole("button", { name: "Context" })).toBeNull()
    expect(screen.queryByText("Pull request context")).toBeNull()
    expect(
      screen.getByText(
        "Moved to the cloud. Continuing from the branch as pushed."
      )
    ).toBeTruthy()
    expect(screen.getByText("Investigate the nightly job failure")).toBeTruthy()
    expect(screen.getByText("Fix the deployment check")).toBeTruthy()
    expect(screen.queryByTestId("system-message-toggle")).toBeNull()
  })

  it("copies the displayed thread URL with the dashboard base path on another page", async () => {
    vi.stubEnv("BASE_URL", "/robin/")
    const writeText = vi.fn().mockResolvedValue(undefined)
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText },
    })
    render(
      <Messages
        threadId="displayed-thread"
        messages={[
          {
            id: "failed-message",
            author: "user",
            timestamp: "2026-11-06T20:18:00Z",
            deliveryStatus: "failed",
            deliveryError: "the message could not be started",
            deliveryTurnId: "failed-turn",
            chunks: [{ kind: "text", text: "When is the job scheduled?" }],
          },
        ]}
        isStreaming={false}
      />,
      { wrapper }
    )
    const failedMessage = screen.getByRole("button", {
      name: "Failed to send · Copy debug details",
    })
    expect(failedMessage.textContent).toBe("Failed to send")
    fireEvent.focus(failedMessage)
    expect((await screen.findByRole("tooltip")).textContent).toContain(
      "the message could not be started"
    )
    await act(async () => {
      fireEvent.click(failedMessage)
    })
    expect(writeText).toHaveBeenCalledWith(
      JSON.stringify(
        {
          url: `${window.location.origin}/robin/agents/displayed-thread`,
          message_id: "failed-message",
          turn_id: "failed-turn",
          timestamp: "2026-11-06T20:18:00Z",
          error: "the message could not be started",
        },
        null,
        2
      )
    )
    Reflect.deleteProperty(navigator, "clipboard")
  })

  it("copies a user's full message and passage replies without metadata", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText },
    })
    Object.defineProperty(window, "isSecureContext", {
      configurable: true,
      value: true,
    })
    const content = "Please investigate this.\n".repeat(30)
    render(
      <Messages
        messages={[
          {
            id: "user-copy",
            author: "user",
            timestamp: "2026-11-06T20:18:00Z",
            timestampIsFallback: true,
            structuredSenderName: "Alice",
            contextLabel: "Private context, not part of the message",
            chunks: [
              { kind: "text", text: content.slice(0, 100) },
              { kind: "text", text: content.slice(100) },
            ],
            replies: [
              {
                source: "conversation",
                before: "Hidden preceding context",
                selection: "The selected passage",
                after: "Hidden following context",
                comment: "Explain this too.",
              },
            ],
          },
        ]}
        isStreaming={false}
      />,
      { wrapper }
    )

    fireEvent.click(screen.getByRole("button", { name: "Copy message" }))
    await vi.waitFor(() =>
      expect(writeText).toHaveBeenCalledWith(
        `The selected passage\n\nExplain this too.\n\n${content}`
      )
    )
    expect(screen.getByRole("button", { name: "Copied" })).toBeTruthy()
    Reflect.deleteProperty(navigator, "clipboard")
  })

  it("does not offer message copying for system notices or image-only messages", () => {
    render(
      <Messages
        messages={[
          {
            id: "system-notice",
            author: "user",
            timestamp: "2026-11-06T20:18:00Z",
            structuredSenderKind: "system",
            chunks: [{ kind: "text", text: "A system notice" }],
          },
          {
            id: "image-only",
            author: "user",
            timestamp: "2026-11-06T20:18:00Z",
            chunks: [
              { kind: "image", base64: "aW1hZ2U=", mimeType: "image/png" },
            ],
          },
        ]}
        isStreaming={false}
      />,
      { wrapper }
    )

    expect(screen.queryByRole("button", { name: "Copy message" })).toBeNull()
  })

  it("renders queued passage replies even without plain text", () => {
    render(
      <Messages
        messages={[]}
        isStreaming={false}
        queuedMessages={[
          {
            id: "quoted-follow-up",
            content: "",
            createdAt: 2000,
            replies: [
              {
                source: "conversation",
                before: "",
                selection: "The selected passage <example>",
                after: "",
                comment: "Please explain this & its tradeoffs.",
              },
            ],
          },
        ]}
      />,
      { wrapper }
    )

    const queued = screen.getByTestId("queued-message")
    expect(queued.querySelector("blockquote")?.textContent).toBe(
      "The selected passage <example>"
    )
    expect(queued.textContent).toContain("Please explain this & its tradeoffs.")
    expect(queued.textContent).toContain("Queued")
    expect(queued.querySelector("example")).toBeNull()
  })

  it("steers only the person's own acknowledged queued messages", () => {
    const onSteer = vi.fn()
    render(
      <Messages
        messages={[]}
        isStreaming
        onRemoveQueuedMessage={vi.fn()}
        onSteerQueuedMessage={onSteer}
        queuedMessages={[
          { id: "mine", content: "Add tests too", createdAt: 1, mine: true },
          {
            id: "sending",
            content: "Still sending",
            createdAt: 2,
            pending: true,
          },
          { id: "theirs", content: "Bob's", createdAt: 3, mine: false },
          {
            id: "inbox",
            content: "For the agent",
            createdAt: 4,
            waitsForAgent: true,
          },
        ]}
      />,
      { wrapper }
    )

    const steer = (text: string) =>
      screen
        .getAllByTestId("queued-message")
        .find((row) => row.textContent?.includes(text))
        ?.querySelector<HTMLButtonElement>('[aria-label="Steer now"]')
    expect(steer("Still sending")?.disabled).toBe(true)
    expect(steer("Bob's")).toBeNull()
    expect(steer("For the agent")).toBeNull()

    fireEvent.click(steer("Add tests too")!)
    expect(onSteer).toHaveBeenCalledExactlyOnceWith("mine")
  })

  it("shows delegated activity while the parent is idle and clears it when settled", () => {
    const { rerender } = render(
      <Messages messages={[]} isStreaming={false} isWaitingForDelegatedWork />,
      { wrapper }
    )

    expect(screen.getByRole("status").textContent).toContain(
      "Waiting for delegated work…"
    )

    rerender(<Messages messages={[]} isStreaming={false} />)
    expect(screen.queryByRole("status")).toBeNull()
  })

  it("links Slack user messages to their original message when metadata is available", () => {
    const messages = streamMessagesToUi([
      new HumanMessage({
        id: "slack-person",
        content:
          '<dynamic-context kind="person" id="slack:U123">\ndisplay_name: Alice\n</dynamic-context>',
      }),
      new HumanMessage({
        id: "slack-message",
        content:
          '<input-message sender="slack:U123" channel="slack:C123" surface="slack" kind="human" timestamp="1787165499.123456" slack_message_ts="1787165487.000034">Hello</input-message>',
      }),
      new HumanMessage({
        id: "legacy-slack-message",
        content:
          '<input-message sender="slack:U123" surface="slack" kind="human">Earlier message</input-message>',
      }),
    ])
    render(<Messages messages={messages} isStreaming={false} />, { wrapper })

    const link = screen.getByRole("link", {
      name: "View message from Alice in Slack",
    })
    expect(link.getAttribute("href")).toBe(
      "/dashboard/api/slack/open?channel=C123&message_ts=1787165487.000034"
    )
    expect(link.getAttribute("target")).toBe("_blank")
    expect(screen.getByRole("img", { name: "Slack" })).toBeTruthy()
  })

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
    const { container } = render(<RouterProvider router={router} />, {
      wrapper,
    })

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
    const result = screen.getByRole("button", {
      name: "Details for Worker result received",
    })
    expect(result.getAttribute("aria-expanded")).toBe("false")
    expect(container.textContent).not.toContain("Can I change")
    fireEvent.click(result)
    expect(container.textContent).toContain(source.content)
    fireEvent.click(result)
    fireEvent.click(details)
    expect(container.textContent).toContain(source.content)
    expect(container.querySelector("img,script")).toBeNull()
    expect(container.textContent).not.toContain("Model-facing safety warning")
    expect(container.textContent).not.toContain("untrusted-worker-output")
    expect(container.textContent).not.toContain("hidden model text")
    fireEvent.click(details)
    expect(container.textContent).not.toContain("Can I change")
  })

  it("shows consecutive read and search calls individually inside the work fold", () => {
    const read = (id: string, path: string): ToolExecutionChunk => ({
      kind: "tool-execution",
      toolCallId: id,
      toolName: "read_file",
      title: "read_file",
      toolKind: "read",
      input: { file_path: path },
      status: "completed",
      output: "File contents",
    })
    const search: ToolExecutionChunk = {
      kind: "tool-execution",
      toolCallId: "search",
      toolName: "grep",
      title: "grep",
      toolKind: "search",
      input: { pattern: "work" },
      status: "completed",
      output: "Matches",
    }
    const messages: Message[] = [
      {
        id: "turn",
        author: "agent",
        timestamp: "2026-01-01T00:00:10Z",
        chunks: [
          read("read-1", "first.ts"),
          search,
          read("read-2", "second.ts"),
          {
            kind: "tool-execution",
            toolCallId: "eval",
            title: "eval",
            toolKind: "code",
            input: { code: "await tools.readFile()" },
            status: "completed",
            calls: [
              read("nested-1", "nested-first.ts"),
              read("nested-2", "nested-second.ts"),
            ],
          },
          { kind: "text", text: "Done." },
        ],
      },
    ]
    render(<Messages messages={messages} isStreaming={false} />, { wrapper })

    const fold = screen.getByRole("button", { name: "Worked · 6 actions" })
    expect(screen.queryByText("first.ts")).toBeNull()
    fireEvent.click(fold)
    const rows = [
      screen.getByText("first.ts"),
      screen.getByText('"work"'),
      screen.getByText("second.ts"),
      screen.getByText("nested-first.ts"),
      screen.getByText("nested-second.ts"),
    ]
    for (let i = 1; i < rows.length; i += 1) {
      expect(
        rows[i - 1]!.compareDocumentPosition(rows[i]!) &
          Node.DOCUMENT_POSITION_FOLLOWING
      ).toBeTruthy()
    }
    expect(
      screen.queryByRole("button", { name: /previous tool calls/i })
    ).toBeNull()
    fireEvent.click(fold)
    expect(screen.queryByText("first.ts")).toBeNull()
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
    const { container } = render(<RouterProvider router={router} />, {
      wrapper,
    })
    const chip = screen.getByRole("button", {
      name: "Details for Worker · Started",
    })
    const fold = screen.getByRole("button", { name: "Worked · 3 actions" })
    expect(fold.getAttribute("aria-expanded")).toBe("false")
    expect(
      fold.compareDocumentPosition(chip) & Node.DOCUMENT_POSITION_FOLLOWING
    ).toBeTruthy()
    fireEvent.click(fold)
    expect(screen.getByText("AGENTS.md")).toBeTruthy()
    expect(screen.getByText("README.md")).toBeTruthy()
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

  it("shows a worker result in order within the work fold and keeps failures clear", async () => {
    const workerId = "7db1bbf5-0623-5a35-a5a4-db372cfc31d4"
    const tool = (id: string, path: string): ToolExecutionChunk => ({
      kind: "tool-execution",
      toolCallId: id,
      toolName: "read_file",
      title: `read_file ${path}`,
      toolKind: "read",
      input: { path },
      status: "completed",
    })
    const messages: Message[] = [
      {
        id: "turn",
        author: "agent",
        timestamp: "2026-01-01T00:00:10Z",
        chunks: [
          tool("before", "before.txt"),
          {
            kind: "task-event",
            messageId: "result",
            event: {
              version: 1,
              task_id: "d505b040-c025-4b52-a27e-339803281cfb",
              sender_thread_id: workerId,
              sender_role: "worker",
              sender_label: "Investigation",
              kind: "completion",
              status: "error",
              content: "The worker failed safely <test>.",
            },
          },
          tool("after", "after.txt"),
          { kind: "text", text: "Continuing." },
        ],
      },
    ]
    const router = createRouter({
      routeTree: createRootRoute({
        component: () => <Messages messages={messages} isStreaming={false} />,
      }),
      history: createMemoryHistory({ initialEntries: ["/"] }),
    })
    await router.load()
    render(<RouterProvider router={router} />, { wrapper })
    const fold = screen.getByRole("button", { name: "Worked · 2 actions" })
    const result = screen.getByRole("button", {
      name: "Details for Worker result received · Failed",
    })
    expect(
      screen
        .getByRole("link", { name: "Open Investigation thread" })
        .getAttribute("href")
    ).toBe(`/agents/${workerId}`)
    fireEvent.click(fold)
    const before = screen.getByText("before.txt")
    const after = screen.getByText("after.txt")
    expect(
      before.compareDocumentPosition(result) & Node.DOCUMENT_POSITION_FOLLOWING
    ).toBeTruthy()
    expect(
      result.compareDocumentPosition(after) & Node.DOCUMENT_POSITION_FOLLOWING
    ).toBeTruthy()
    fireEvent.click(result)
    expect(screen.getByText("The worker failed safely <test>.")).toBeTruthy()
  })

  it.each(["manage_thread", "message_task_thread"])(
    "keeps a successful %s message between separate work folds",
    (name) => {
      const recipient = "553f74de-534d-5377-815d-c14b58b21ea2"
      const messages = streamMessagesToUi([
        new AIMessage({
          id: "routed-turn",
          content: "",
          tool_calls: [
            { id: "read", name: "read_file", args: { file_path: "AGENTS.md" } },
            {
              id: "send",
              name,
              args: {
                action: "send_message",
                message: "Check token validation",
              },
            },
            {
              id: "later-read",
              name: "read_file",
              args: { file_path: "README.md" },
            },
          ],
        }),
        new ToolMessage({ tool_call_id: "read", content: "Instructions" }),
        new ToolMessage({
          tool_call_id: "send",
          content: JSON.stringify({
            success: true,
            recipient_thread_id: recipient,
          }),
        }),
        new ToolMessage({ tool_call_id: "later-read", content: "Docs" }),
      ])
      render(<Messages messages={messages} isStreaming={false} />, { wrapper })

      const link = screen.getByRole("link", { name: "Open destination thread" })
      expect(link.getAttribute("href")).toBe(`/agents/${recipient}`)
      const folds = screen.getAllByRole("button", { name: "Worked · 1 action" })
      expect(folds).toHaveLength(2)
      expect(
        folds.every((fold) => fold.getAttribute("aria-expanded") === "false")
      ).toBe(true)
      expect(
        folds[0]!.compareDocumentPosition(link) &
          Node.DOCUMENT_POSITION_FOLLOWING
      ).toBeTruthy()
      expect(
        link.compareDocumentPosition(folds[1]!) &
          Node.DOCUMENT_POSITION_FOLLOWING
      ).toBeTruthy()
      expect(screen.queryByText("AGENTS.md")).toBeNull()
      fireEvent.click(folds[0]!)
      expect(screen.getByText("AGENTS.md")).toBeTruthy()
      expect(screen.queryByText("README.md")).toBeNull()
    }
  )

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
        />,
        { wrapper }
      )
      expect(
        screen.getByRole("button", {
          name:
            label === "Launch status unknown"
              ? `Worker · ${label}`
              : `Details for Worker · ${label}`,
        })
      ).toBeTruthy()
      expect(screen.queryByRole("link")).toBeNull()
    }
  )

  it("folds runs that woke and ended without a reply into one row", () => {
    const monitor = (
      id: string,
      at: string,
      sender = { id: "system:monitor", name: "Monitor" }
    ) => ({
      id,
      author: "user" as const,
      timestamp: at,
      structuredSenderKind: "system" as const,
      structuredSenderId: sender.id,
      structuredSenderName: sender.name,
      chunks: [
        {
          kind: "text" as const,
          text:
            sender.id === "system:background-task"
              ? "A sandbox background command finished.\nTask: cmd-123\nStatus: completed\nExit code: 0\nDuration: 33.27s\nOutput: /tmp/output.log\nUse background_task to continue."
              : `Monitor \`${id}\` (CI) emitted, starting at raw line 1:\nsecret instruction`,
        },
      ],
    })
    const silentRun = (
      id: string,
      startedAt: string,
      at: string,
      call: Partial<ToolExecutionChunk> = {}
    ) => ({
      id,
      author: "agent" as const,
      startedAt,
      timestamp: at,
      chunks: [
        {
          kind: "tool-execution" as const,
          toolCallId: `${id}-call`,
          toolName: "check_pr",
          toolKind: "other" as const,
          title: "Check PR",
          status: "completed" as const,
          output: "still pending",
          ...call,
        },
      ],
    })
    render(
      <Messages
        isStreaming={false}
        messages={[
          {
            id: "moved",
            author: "user",
            timestamp: "2026-09-03T09:59:00.000Z",
            structuredSenderKind: "system",
            structuredSenderId: "system:sandbox-handoff",
            structuredSenderName: "Moved to the cloud",
            chunks: [{ kind: "text", text: "Moved this thread to the cloud." }],
          },
          monitor("wake-1", "2026-09-03T10:00:00.000Z"),
          silentRun(
            "run-1",
            "2026-09-03T10:00:00.000Z",
            "2026-09-03T10:01:00.000Z"
          ),
          monitor("wake-2", "2026-09-03T10:05:00.000Z", {
            id: "system:background-task",
            name: "Background task",
          }),
          silentRun(
            "run-2",
            "2026-09-03T10:05:00.000Z",
            "2026-09-03T10:08:00.000Z"
          ),
          monitor("wake-3", "2026-09-03T10:10:00.000Z"),
          {
            id: "run-3",
            author: "agent",
            timestamp: "2026-09-03T10:11:00.000Z",
            chunks: [{ kind: "text", text: "CI is green." }],
          },
          // A call waiting on approval keeps its run, and the one before it,
          // out of a fold.
          monitor("wake-4", "2026-09-03T10:20:00.000Z"),
          silentRun(
            "run-4",
            "2026-09-03T10:20:00.000Z",
            "2026-09-03T10:21:00.000Z"
          ),
          monitor("wake-5", "2026-09-03T10:25:00.000Z"),
          silentRun(
            "run-5",
            "2026-09-03T10:25:00.000Z",
            "2026-09-03T10:26:00.000Z",
            { status: "pending" }
          ),
          monitor("wake-6", "2026-09-03T10:30:00.000Z"),
          silentRun(
            "run-6",
            "2026-09-03T10:30:00.000Z",
            "2026-09-03T10:31:00.000Z",
            { toolKind: "read", status: "error", output: "Check failed" }
          ),
          monitor("wake-7", "2026-09-03T10:35:00.000Z"),
          silentRun(
            "run-7",
            "2026-09-03T10:35:00.000Z",
            "2026-09-03T10:36:00.000Z"
          ),
          monitor("wake-8", "2026-09-03T10:40:00.000Z"),
          silentRun(
            "run-8",
            "2026-09-03T10:40:00.000Z",
            "2026-09-03T10:41:00.000Z",
            {
              toolKind: "code",
              calls: [
                {
                  kind: "tool-execution",
                  toolCallId: "nested-failed-call",
                  toolName: "check_pr",
                  toolKind: "other",
                  title: "Check PR",
                  status: "error",
                  output: "Nested check failed",
                },
              ],
            }
          ),
        ]}
      />,
      { wrapper }
    )

    const fold = screen.getByRole("button", {
      name: "2 runs without a reply · 4m 0s · 2 actions · woken by Monitor, Background task",
    })
    expect(
      screen.getAllByRole("button", { name: /runs without a reply/ })
    ).toHaveLength(1)
    expect(screen.queryByRole("button", { name: "Monitor" })).toBeNull()
    expect(screen.getByText("CI is green.")).toBeTruthy()
    expect(
      screen.queryByRole("button", { name: "Moved to the cloud" })
    ).toBeNull()

    fireEvent.click(fold)
    expect(fold.getAttribute("aria-expanded")).toBe("true")
    expect(screen.getAllByText("Monitor update").length).toBeGreaterThan(1)
    const task = screen.getByRole("button", {
      name: "Background task completed · 33s",
    })
    fireEvent.click(task)
    expect(screen.getByText(/Task: cmd-123/)).toBeTruthy()
    expect(screen.queryByText(/secret instruction/)).toBeNull()
    expect(screen.queryByText(/Use background_task/)).toBeNull()
    fireEvent.click(
      screen.getByRole("button", { name: "Worked for 1m 0s · 2 actions" })
    )
    expect(screen.getByText("Failed")).toBeTruthy()
  })

  it("does not present a subscription deadline as a failure", () => {
    const message: Message = {
      id: "subscription-timeout",
      author: "system",
      timestamp: "2026-09-03T10:00:00Z",
      structuredSenderKind: "system",
      structuredSenderId: "system:event-subscription",
      chunks: [
        {
          kind: "text",
          text: "The bounded wait for subscription `sub-123` on GitHub has reached its deadline.",
        },
      ],
    }
    expect(BackgroundEvent.fromMessage(message)).toEqual({
      heading: "Event subscription timed out",
      failed: false,
    })
  })

  it("distinguishes failed and timed-out background events without exposing model instructions", () => {
    render(
      <Messages
        isStreaming={false}
        messages={[
          {
            id: "failed",
            author: "system",
            timestamp: "2026-09-03T10:00:00Z",
            structuredSenderKind: "system",
            structuredSenderId: "system:background-task",
            chunks: [
              {
                kind: "text",
                text: "A sandbox background command finished.\nTask: cmd-123\nStatus: failed\nExit code: 1\nDuration: 3s\nOutput: /tmp/output.log\nUse background_task to continue.",
              },
            ],
          },
          {
            id: "timeout",
            author: "system",
            timestamp: "2026-09-03T10:01:00Z",
            structuredSenderKind: "system",
            structuredSenderId: "system:background-task",
            chunks: [
              {
                kind: "text",
                text: "A sandbox background command finished.\nTask: cmd-456\nStatus: timed_out\nExit code: None\nDuration: 10s\nOutput: /tmp/output.log",
              },
            ],
          },
          {
            id: "monitor",
            author: "system",
            timestamp: "2026-09-03T10:02:00Z",
            structuredSenderKind: "system",
            structuredSenderId: "system:monitor",
            chunks: [
              {
                kind: "text",
                text: "Monitor `mon-123` (CI) ended: it reached its 30s timeout.\nSECRET INSTRUCTIONS",
              },
            ],
          },
        ]}
      />,
      { wrapper }
    )
    expect(
      screen.getByRole("button", { name: "Background task failed · 3s" })
    ).toBeTruthy()
    expect(
      screen.getByRole("button", { name: "Background task timed out · 10s" })
    ).toBeTruthy()
    expect(screen.getByText("Monitor timed out")).toBeTruthy()
    expect(screen.queryByText(/SECRET INSTRUCTIONS/)).toBeNull()
    expect(screen.queryByText(/Use background_task/)).toBeNull()
  })

  it("shows run activity while a stream is starting with no messages", () => {
    const { rerender } = render(<Messages messages={[]} isStreaming />)

    expect(screen.getByRole("status").textContent).toBe("Thinking…")
    const wake: Message = {
      id: "wake",
      author: "user",
      timestamp: new Date().toISOString(),
      structuredSenderKind: "system",
      structuredSenderId: "system:monitor",
      chunks: [{ kind: "text", text: "Check CI" }],
    }
    rerender(
      <Messages
        messages={[wake]}
        isStreaming
        emptyState={<p>No messages yet</p>}
      />
    )
    expect(screen.getByRole("status").textContent).toContain("Thinking…")
    expect(screen.queryByText("No messages yet")).toBeNull()
    expect(screen.queryByText("Check CI")).toBeNull()
    rerender(
      <Messages
        messages={[
          {
            id: "previous-answer",
            author: "agent",
            timestamp: wake.timestamp,
            chunks: [{ kind: "text", text: "Previous answer" }],
          },
          wake,
        ]}
        isStreaming
      />
    )
    expect(screen.getByText("Previous answer")).toBeTruthy()
    expect(screen.getByRole("status").textContent).toContain("Thinking…")
    rerender(
      <Messages
        messages={[wake]}
        isStreaming={false}
        emptyState={<p>No messages yet</p>}
      />
    )
    expect(screen.getByText("No messages yet")).toBeTruthy()
  })

  it("follows nested tools and keeps the run clock across replies", () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date("2026-09-03T10:00:30Z"))
    const call: ToolExecutionChunk = {
      kind: "tool-execution",
      toolCallId: "eval",
      title: "Eval",
      toolKind: "code",
      status: "in_progress",
      input: { display_description: "Checking documents" },
      calls: [
        {
          kind: "tool-execution",
          toolCallId: "slack-search",
          title: "Slack: Search messages",
          toolKind: "other",
          status: "in_progress",
        },
      ],
    }
    const message: Message = {
      id: "live-turn",
      author: "agent",
      runStartedAt: "2026-09-03T10:00:00Z",
      startedAt: "2026-09-03T10:00:05Z",
      timestamp: "2026-09-03T10:00:20Z",
      chunks: [call, { kind: "reasoning", text: "**Next steps**" }],
    }
    const view = render(<Messages messages={[message]} isStreaming />, {
      wrapper,
    })
    const activity = screen.getByRole("button", {
      name: "Using Slack: Search Messages…",
    })
    expect(activity.textContent).toContain("30s")
    expect(activity.textContent).not.toContain("action")
    expect(
      screen.queryByRole("button", { name: "Details for Eval" })
    ).toBeNull()
    expect(screen.queryByText("Next steps")).toBeNull()
    fireEvent.click(activity)
    expect(
      screen.getByRole("button", { name: "Details for Eval" })
    ).toBeTruthy()
    fireEvent.click(activity)
    expect(
      screen.queryByRole("button", { name: "Details for Eval" })
    ).toBeNull()
    act(() => vi.advanceTimersByTime(5000))
    const next: Message = {
      ...message,
      chunks: [
        { ...call, status: "completed" },
        {
          kind: "text",
          text: "I found the document.",
          timestamp: "2026-09-03T10:00:32Z",
        },
        {
          ...call,
          toolCallId: "checks",
          calls: undefined,
          input: { display_description: "Running checks" },
        },
      ],
    }
    view.rerender(<Messages messages={[next]} isStreaming />)
    expect(
      screen.getByRole("button", { name: /Running checks/ }).textContent
    ).toContain("3s")
    view.rerender(
      <Messages
        messages={[
          {
            ...next,
            chunks: next.chunks.map((chunk) =>
              chunk.kind === "tool-execution"
                ? { ...chunk, status: "completed" }
                : chunk
            ),
          },
        ]}
        isStreaming
      />
    )
    expect(screen.getByRole("button", { name: /Running checks/ })).toBeTruthy()
    act(() => vi.advanceTimersByTime(600))
    expect(
      screen.getByRole("button", { name: /Thinking…/ }).textContent
    ).toContain("3s")
    view.rerender(<Messages messages={[next]} isStreaming settingUpSandbox />)
    expect(
      screen.getByRole("button", { name: /Agent is setting up/ })
    ).toBeTruthy()
    view.rerender(<Messages messages={[next]} isStreaming={false} />)
    expect(screen.queryByRole("button", { name: /Running checks/ })).toBeNull()
    view.unmount()
    vi.useRealTimers()
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
      "Reconnecting… 3/12 (retrying in 4s)"
    )
  })

  it("keeps work time when returning to a running thread and when it finishes", () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date("2026-09-03T10:00:30Z"))
    const messages: Message[] = [
      {
        id: "agent-turn",
        author: "agent",
        runStartedAt: "2026-09-03T10:00:00Z",
        startedAt: "2026-09-03T10:00:05Z",
        timestamp: "2026-09-03T10:00:12Z",
        chunks: [
          {
            kind: "text",
            text: "I'll investigate.",
            timestamp: "2026-09-03T10:00:10Z",
          },
          {
            kind: "tool-execution",
            toolCallId: "search",
            title: "Search",
            toolKind: "search",
            status: "in_progress",
            timestamp: "2026-09-03T10:00:12Z",
          },
        ],
      },
    ]
    const view = render(<Messages messages={messages} isStreaming />, {
      wrapper,
    })
    view.unmount()
    vi.setSystemTime(new Date("2026-09-03T10:00:35Z"))
    const reopened = render(<Messages messages={messages} isStreaming />, {
      wrapper,
    })
    expect(
      screen.getByRole("button", { name: "Searching…" }).textContent
    ).toContain("35s")
    act(() => vi.advanceTimersByTime(5000))
    reopened.rerender(<Messages messages={messages} isStreaming={false} />)
    expect(
      screen.getByRole("button", { name: "Worked for 40s · 1 action" })
    ).toBeTruthy()
    reopened.unmount()
    render(
      <Messages
        messages={[{ ...messages[0]!, completedAt: "2026-09-03T10:00:40Z" }]}
        isStreaming={false}
      />,
      { wrapper }
    )
    expect(
      screen.getByRole("button", { name: "Worked for 40s · 1 action" })
    ).toBeTruthy()
    vi.useRealTimers()
  })

  it("keeps live stretch elapsed time aligned with its settled duration", () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date("2026-09-03T10:00:35Z"))
    const message: Message = {
      id: "agent-turn",
      author: "agent",
      runStartedAt: "2026-09-03T10:00:00Z",
      startedAt: "2026-09-03T10:00:01Z",
      timestamp: "2026-09-03T10:00:21Z",
      chunks: [
        {
          kind: "tool-execution",
          toolCallId: "first",
          title: "Search",
          toolKind: "search",
          status: "completed",
          timestamp: "2026-09-03T10:00:01Z",
        },
        {
          kind: "text",
          text: "Found the cause.",
          timestamp: "2026-09-03T10:00:20Z",
        },
        {
          kind: "tool-execution",
          toolCallId: "second",
          title: "Search",
          toolKind: "search",
          status: "in_progress",
          timestamp: "2026-09-03T10:00:21Z",
        },
      ],
    }
    const view = render(<Messages messages={[message]} isStreaming />, {
      wrapper,
    })
    expect(
      screen.getByRole("button", { name: "Worked for 20s · 1 action" })
    ).toBeTruthy()
    expect(
      screen.getByRole("button", { name: "Searching…" }).textContent
    ).toContain("15s")
    view.unmount()
    const reopened = render(<Messages messages={[message]} isStreaming />, {
      wrapper,
    })
    expect(
      screen.getByRole("button", { name: "Searching…" }).textContent
    ).toContain("15s")
    act(() => vi.advanceTimersByTime(5000))
    reopened.rerender(<Messages messages={[message]} isStreaming={false} />)
    expect(
      screen.getAllByRole("button", { name: "Worked for 20s · 1 action" })
    ).toHaveLength(2)
    reopened.unmount()
    render(
      <Messages
        messages={[{ ...message, completedAt: "2026-09-03T10:00:40Z" }]}
        isStreaming={false}
      />,
      { wrapper }
    )
    expect(
      screen.getAllByRole("button", { name: "Worked for 20s · 1 action" })
    ).toHaveLength(2)
    vi.useRealTimers()
  })

  it("exposes failed subagent task output behind the work fold disclosure", async () => {
    const loadOutput = vi.fn().mockResolvedValue("Provider returned 503")
    const message: Message = {
      id: "subagent-turn",
      author: "agent",
      runStartedAt: "2026-09-03T10:00:00Z",
      startedAt: "2026-09-03T10:00:00Z",
      timestamp: "2026-09-03T10:00:05Z",
      chunks: [
        {
          kind: "tool-execution",
          toolCallId: "task-1",
          title: "task",
          toolKind: "task",
          status: "error",
          input: {
            subagent_type: "general-purpose",
            description: "Investigate test failure",
          },
          loadOutput,
        },
      ],
    }

    render(<Messages messages={[message]} isStreaming />, { wrapper })
    fireEvent.click(screen.getByRole("button", { name: "Thinking…" }))
    const details = screen.getByRole("button", { name: "Error details" })
    expect(details.getAttribute("aria-expanded")).toBe("false")
    expect(loadOutput).not.toHaveBeenCalled()
    fireEvent.click(details)
    expect(details.getAttribute("aria-expanded")).toBe("true")
    expect(await screen.findByText("Provider returned 503")).toBeTruthy()
    expect(loadOutput).toHaveBeenCalledTimes(1)
  })

  it("times work after an interjection from the new agent message", () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date("2026-09-03T10:00:40Z"))
    const messages: Message[] = [
      {
        id: "first",
        author: "agent",
        runStartedAt: "2026-09-03T10:00:00Z",
        startedAt: "2026-09-03T10:00:01Z",
        timestamp: "2026-09-03T10:00:20Z",
        chunks: [
          {
            kind: "tool-execution",
            toolCallId: "first",
            title: "Search",
            toolKind: "search",
            status: "completed",
          },
        ],
      },
      {
        id: "human",
        author: "user",
        timestamp: "2026-09-03T10:00:25Z",
        chunks: [{ kind: "text", text: "Continue" }],
      },
      {
        id: "second",
        author: "agent",
        startedAt: "2026-09-03T10:00:30Z",
        timestamp: "2026-09-03T10:00:30Z",
        chunks: [
          {
            kind: "tool-execution",
            toolCallId: "second",
            title: "Search",
            toolKind: "search",
            status: "in_progress",
          },
        ],
      },
    ]
    const view = render(<Messages messages={messages} isStreaming />, {
      wrapper,
    })
    expect(
      screen.getByRole("button", { name: "Worked for 20s · 1 action" })
    ).toBeTruthy()
    expect(
      screen.getByRole("button", { name: "Searching…" }).textContent
    ).toContain("10s")
    view.unmount()
    render(
      <Messages
        messages={[
          messages[0]!,
          messages[1]!,
          { ...messages[2]!, completedAt: "2026-09-03T10:00:50Z" },
        ]}
        isStreaming={false}
      />,
      { wrapper }
    )
    expect(
      screen.getAllByRole("button", { name: "Worked for 20s · 1 action" })
    ).toHaveLength(2)
    vi.useRealTimers()
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
      name: "Running…",
    })
    expect(
      reply.compareDocumentPosition(work) & Node.DOCUMENT_POSITION_FOLLOWING
    ).toBeTruthy()
  })

  it("expands a tool call to its arguments as formatted JSON", () => {
    const { container } = render(
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
                toolCallId: "report",
                title: "report_platform_issue",
                toolKind: "other",
                status: "completed",
                input: {
                  problem_description: "Sandbox disconnected",
                  keywords: ["sandbox"],
                },
                output: "Recorded",
              },
              {
                kind: "tool-execution",
                toolCallId: "ping",
                title: "ping_service",
                toolKind: "other",
                status: "completed",
              },
            ],
          },
        ]}
      />,
      { wrapper }
    )

    fireEvent.click(screen.getByRole("button", { name: /Worked/ }))
    expect(screen.getByText("Ping service").closest("[aria-expanded]")).toBe(
      null
    )
    fireEvent.click(
      screen.getByRole("button", {
        name: "Details for Report platform issue",
      })
    )
    expect(container.textContent).toContain(
      JSON.stringify(
        {
          problem_description: "Sandbox disconnected",
          keywords: ["sandbox"],
        },
        null,
        2
      )
    )
    expect(container.textContent).toContain("Recorded")
  })

  it("folds each stretch of work between the Slack replies around it", () => {
    vi.useFakeTimers()
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

    // A run that ends right after a reply never flashes an activity row under it.
    expect(screen.queryByRole("button", { name: "Thinking…" })).toBeNull()
    act(() => vi.advanceTimersByTime(800))
    vi.useRealTimers()

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
            runStartedAt: "2026-09-03T10:00:00.000Z",
            completedAt: "2026-09-03T10:10:00.000Z",
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

  it("shows a tool result's images on its card outside the folded work", async () => {
    const dataUrl = "data:image/png;base64,iVBORw0KGgo="
    // A screenshot the state read links to, which loads from the transcript's copy.
    const stored = attachmentUrl("t-1", "a-1")
    const fetchImage = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(new Response(new Blob(["png"], { type: "image/png" })))
    Object.assign(URL, {
      createObjectURL: () => "blob:stored-shot",
      revokeObjectURL: () => {},
    })
    const messages = streamMessagesToUi([
      new AIMessage({
        id: "browse",
        content: "",
        tool_calls: [
          { id: "ls", name: "execute", args: { command: "ls" } },
          { id: "shot", name: "screenshot", args: {} },
        ],
      }),
      new ToolMessage({ tool_call_id: "ls", content: "README.md" }),
      new ToolMessage({
        tool_call_id: "shot",
        // The block MDA's browser `screenshot` tool returns.
        content: [
          { type: "text", text: "Captured the page" },
          { type: "image", url: dataUrl },
          {
            type: "image",
            url: "/dashboard/api/threads/t-1/transcript/attachments/a-1",
            mime_type: "image/png",
          },
        ],
      }),
      new AIMessage({ id: "reply", content: "The page is shown above." }),
    ])
    render(<Messages messages={messages} isStreaming={false} />, { wrapper })

    expect(
      screen.getByRole("button", { name: "Worked · 1 action" })
    ).toBeTruthy()
    expect(screen.queryByText("README.md")).toBeNull()
    await waitFor(() =>
      expect(
        screen
          .getAllByRole("img", { name: "image" })
          .map((image) => image.getAttribute("src"))
      ).toEqual([dataUrl, "blob:stored-shot"])
    )
    expect(fetchImage).toHaveBeenCalledWith(stored, { credentials: "include" })
    expect(screen.getByText("Captured the page")).toBeTruthy()
    fetchImage.mockRestore()
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

  it("dates approval cards by the thread's newest user message, also when it's older than the turns shown", () => {
    const ask: Message = {
      id: "ask",
      author: "user",
      timestamp: "2026-11-17T10:00:00Z",
      chunks: [{ kind: "text", text: "Ship it" }],
    }
    const answer: Message = {
      id: "answer",
      author: "agent",
      timestamp: "2026-11-17T10:05:00Z",
      chunks: [{ kind: "text", text: "Done" }],
    }
    render(
      <Messages
        threadId="thread-1"
        messages={[answer]}
        transcript={[ask, answer]}
        isStreaming={false}
      />,
      { wrapper }
    )
    expect(
      screen.getByTestId("workflow-approval-card").dataset.latestUserMessageAt
    ).toBe("2026-11-17T10:00:00Z")
  })
})
