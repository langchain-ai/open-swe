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
  useAuiState,
} from "@assistant-ui/react"
import type { RemoteThreadListAdapter } from "@assistant-ui/react"
import { useStreamRuntime } from "@assistant-ui/react-langchain"
import type { UseStreamRuntimeOptions } from "@assistant-ui/react-langchain"

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
