/** @vitest-environment jsdom */
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react"
import { afterEach, expect, it, vi } from "vitest"
import {
  AssistantRuntimeProvider,
  ComposerPrimitive,
  QueueItemPrimitive,
  SimpleImageAttachmentAdapter,
  useAui,
  useAuiState,
} from "@assistant-ui/react"
import type { RemoteThreadListAdapter } from "@assistant-ui/react"
import { useStreamRuntime } from "@assistant-ui/react-langchain"
import type { UseStreamRuntimeOptions } from "@assistant-ui/react-langchain"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import type { AgentThread } from "@/features/agents/lib/types"
import { useServerQueue } from "./serverQueue"

const mocks = vi.hoisted(() => ({ useStream: vi.fn(), empty: [] }))
vi.mock("@langchain/react", () => ({
  STREAM_CONTROLLER: Symbol("controller"),
  useStream: mocks.useStream,
  useChannel: () => mocks.empty,
}))
afterEach(cleanup)

const adapter: RemoteThreadListAdapter = {
  list: async () => ({ threads: [] }),
  initialize: async () => ({ remoteId: "created", externalId: "created" }),
  fetch: async (id) => ({ remoteId: id, externalId: id, status: "regular" }),
  rename: async () => {},
  archive: async () => {},
  unarchive: async () => {},
  delete: async () => {},
  generateTitle: async () =>
    new ReadableStream({
      start(controller) {
        controller.close()
      },
    }),
}

function mockStream(running = false) {
  const stream = {
    messages: [],
    values: {},
    isLoading: running,
    isThreadLoading: false,
    toolCalls: [],
    subagents: new Map(),
    subgraphs: new Map(),
    interrupts: [],
    interrupt: undefined,
    error: undefined,
    submit: vi.fn(async () => {}),
    stop: vi.fn(async () => {}),
    respond: vi.fn(),
    respondAll: vi.fn(),
    client: {},
  }
  mocks.useStream.mockReturnValue(stream)
  return stream
}

function Body() {
  const aui = useAui()
  const attached = useAuiState((state) => state.composer.attachments.length)
  const extras = useAuiState((state) => state.thread.extras)
  if (!extras) return null
  return (
    <>
      <span>
        {String(
          typeof extras === "object" && "productStatus" in extras
            ? extras.productStatus
            : ""
        )}
      </span>
      <ComposerPrimitive.Root>
        <button
          onClick={() =>
            void aui
              .composer()
              .addAttachment(
                new File(["image"], "test.png", { type: "image/png" })
              )
          }
        >
          Attach
        </button>
        <span>Attachments: {attached}</span>
        <ComposerPrimitive.Input aria-label="Draft" />
        <ComposerPrimitive.Send>Send</ComposerPrimitive.Send>
        <ComposerPrimitive.Cancel>Stop</ComposerPrimitive.Cancel>
        <ComposerPrimitive.Queue>
          {() => <QueueItemPrimitive.Text />}
        </ComposerPrimitive.Queue>
      </ComposerPrimitive.Root>
    </>
  )
}

function Harness({ options }: { options: Partial<UseStreamRuntimeOptions> }) {
  const runtime = useStreamRuntime({
    assistantId: "agent",
    apiUrl: "http://localhost:2024",
    unstable_threadListAdapter: adapter,
    ...options,
  } as UseStreamRuntimeOptions)
  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <Body />
    </AssistantRuntimeProvider>
  )
}

it("shows a server queue through native primitives and dispatches through the official sender", async () => {
  const stream = mockStream()
  render(
    <Harness
      options={{
        extras: { productStatus: "Server connected" },
        createQueueAdapter: ({ send }) => ({
          items: [
            {
              id: "pending",
              prompt: "Queued on server",
              parts: [{ type: "text", text: "Queued on server" }],
            },
          ],
          steerItems: [],
          enqueue: (message) => {
            void send(message)
          },
          steer: (message) => {
            void send(message)
          },
          move: () => {},
          edit: () => {},
          remove: () => {},
        }),
      }}
    />
  )
  expect(await screen.findByText("Queued on server")).toBeTruthy()
  expect(screen.getByText("Server connected")).toBeTruthy()
  fireEvent.change(screen.getByRole("textbox", { name: "Draft" }), {
    target: { value: "New request" },
  })
  fireEvent.click(screen.getByText("Send"))
  await waitFor(() => expect(stream.submit).toHaveBeenCalledOnce())
  expect(stream.submit).toHaveBeenCalledWith(
    {
      messages: [
        expect.objectContaining({ type: "human", content: "New request" }),
      ],
    },
    { threadId: "created" }
  )
})

it("routes native cancellation through the server cancellation adapter", async () => {
  const stream = mockStream(true)
  const cancel = vi.fn(async () => {})
  render(<Harness options={{ onCancel: cancel }} />)
  fireEvent.click(await screen.findByText("Stop"))
  await waitFor(() => expect(cancel).toHaveBeenCalledWith(stream))
  expect(stream.stop).not.toHaveBeenCalled()
})

const existingThread: AgentThread = {
  id: "created",
  title: "Existing conversation",
  repo: "",
  repoFullName: "",
  branch: "main",
  model: "",
  status: "idle",
  viewed: true,
  createdAt: 1,
  updatedAt: 1,
  messages: [],
}

const imageAdapter = new SimpleImageAttachmentAdapter()

function QueuedHarness({ thread }: { thread?: AgentThread }) {
  const { createQueueAdapter, queueErrors } = useServerQueue(thread?.id, thread)
  return (
    <Harness
      options={{
        createQueueAdapter,
        extras: { productStatus: Object.values(queueErrors).join("\n") },
        adapters: { attachments: imageAdapter },
      }}
    />
  )
}

function renderQueue(thread: AgentThread | undefined = existingThread) {
  return render(
    <QueryClientProvider client={new QueryClient()}>
      <QueuedHarness thread={thread} />
    </QueryClientProvider>
  )
}

async function sendOffload() {
  fireEvent.change(await screen.findByRole("textbox", { name: "Draft" }), {
    target: { value: "/offload" },
  })
  fireEvent.click(screen.getByText("Send"))
}

it("offloads through the SDK without appending a human message", async () => {
  const stream = mockStream()
  renderQueue()
  await sendOffload()
  await waitFor(() =>
    expect(stream.submit).toHaveBeenCalledWith(
      {},
      { config: { configurable: { offload_conversation: true } } }
    )
  )
  expect(stream.submit).toHaveBeenCalledOnce()
  expect(screen.queryByText("/offload")).toBeNull()
})

it.each([
  { running: true, thread: existingThread },
  { running: false, thread: { ...existingThread, status: "running" as const } },
])(
  "rejects offloading while a run is active: %j",
  async ({ running, thread }) => {
    const stream = mockStream(running)
    renderQueue(thread)
    await sendOffload()
    expect(
      await screen.findByText(
        "Wait for the current run to finish before offloading."
      )
    ).toBeTruthy()
    expect(stream.submit).not.toHaveBeenCalled()
  }
)

it("rejects offloading with an attachment", async () => {
  const stream = mockStream()
  renderQueue()
  fireEvent.click(await screen.findByText("Attach"))
  await screen.findByText("Attachments: 1")
  await sendOffload()
  expect(
    await screen.findByText("Offloading does not accept attachments.")
  ).toBeTruthy()
  expect(stream.submit).not.toHaveBeenCalled()
})
