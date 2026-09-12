/** @vitest-environment jsdom */
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"
import { ComposerPrimitive, useAuiState } from "@assistant-ui/react"
import { AIMessage } from "@langchain/core/messages"
import { ExperimentalRuntimeProvider } from "./ExperimentalRuntimeProvider"
import type { ConversationRuntimeExtras } from "./conversationRuntime"
import { useAgentStream } from "@/features/agents/lib/stream/AgentStreamProvider"

const mocks = vi.hoisted(() => ({
  controllers: new Map<
    string,
    { complete(): void; start(): void; unmounted: boolean }
  >(),
  nextDraft: 0,
  fetchGate: undefined as Promise<void> | undefined,
}))

vi.mock("@/features/agents/lib/api", () => ({
  agentsApi: {
    langGraphApiUrl: "/graph",
    listThreadsPage: async () => ({
      items: [],
      offset: 0,
      limit: 100,
      hasMore: false,
    }),
    getThread: async (id: string) => {
      if (id === "missing") throw new Error("Not found")
      if (id === "other") await mocks.fetchGate
      return { id, title: id }
    },
  },
}))
vi.mock("@/lib/langgraph-client", () => ({
  createDashboardClient: () => ({ transport: "cloud" }),
  createLocalGraphClient: () => ({ transport: "local" }),
  dashboardFetch: fetch,
}))
vi.mock("@langchain/react", () => ({
  useChannelEffect: () => {},
  useStream: (options: {
    threadId: string | null
    client: { transport: string }
    onCreated(): void
    onCompleted(): void
  }) => {
    const [draftId] = useState(() => `draft-${++mocks.nextDraft}`)
    const id = options.threadId ?? draftId
    const [isLoading, setRunning] = useState(false)
    const [messages, setMessages] = useState<AIMessage[]>([])
    const toolCalls = useMemo(() => [], [])
    const callbacks = useRef(options)
    useLayoutEffect(() => {
      callbacks.current = options
    }, [options])
    const transport = options.client.transport
    useEffect(() => {
      const controller = {
        unmounted: false,
        start() {
          setRunning(true)
          callbacks.current.onCreated()
        },
        complete() {
          setRunning(false)
          setMessages([
            new AIMessage({ id: `${id}-answer`, content: `Answer for ${id}` }),
          ])
          callbacks.current.onCompleted()
        },
      }
      mocks.controllers.set(`${transport}:${id}`, controller)
      return () => {
        controller.unmounted = true
      }
    }, [id, transport])
    return {
      threadId: options.threadId,
      isLoading,
      messages,
      toolCalls,
      isThreadLoading: false,
      submit: vi.fn(),
    }
  },
}))

function Probe() {
  const stream = useAgentStream()
  const { configureComposer } = useAuiState(
    (s) => s.thread.extras
  ) as ConversationRuntimeExtras
  useLayoutEffect(() => {
    configureComposer({})
    return () => configureComposer(undefined)
  }, [configureComposer])
  const message = useAuiState((s) => s.thread.messages.at(-1))
  return (
    <>
      <output data-testid="identity">{stream.threadId ?? "new"}</output>
      <output data-testid="answer">
        {message?.content
          .filter((part) => part.type === "text")
          .map((part) => part.text)
          .join("")}
      </output>
      <button
        onClick={() => {
          void stream.submit({ messages: [] })
        }}
      >
        Start thread
      </button>
      <ComposerPrimitive.Root>
        <ComposerPrimitive.Input aria-label="Draft" />
      </ComposerPrimitive.Root>
    </>
  )
}

afterEach(() => {
  cleanup()
  mocks.controllers.clear()
  mocks.fetchGate = undefined
  vi.unstubAllGlobals()
})

describe("experimental thread ownership", () => {
  it("keeps background runs and drafts attached to their thread when switching", async () => {
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    })
    const view = (id: string) => (
      <QueryClientProvider client={client}>
        <ExperimentalRuntimeProvider threadId={id} cloudEnabled>
          <Probe />
        </ExperimentalRuntimeProvider>
      </QueryClientProvider>
    )
    const rendered = render(view("one"))
    await waitFor(() =>
      expect(screen.getByTestId("identity").textContent).toBe("one")
    )
    fireEvent.change(screen.getByRole("textbox", { name: "Draft" }), {
      target: { value: "First draft" },
    })
    const first = mocks.controllers.get("cloud:one")!
    act(() => first.start())
    rendered.rerender(view("two"))
    await waitFor(() =>
      expect(screen.getByTestId("identity").textContent).toBe("two")
    )
    expect(first.unmounted).toBe(false)
    expect(
      (screen.getByRole("textbox", { name: "Draft" }) as HTMLTextAreaElement)
        .value
    ).toBe("")
    fireEvent.change(screen.getByRole("textbox", { name: "Draft" }), {
      target: { value: "Second draft" },
    })
    act(() => first.complete())
    expect(screen.getByTestId("answer").textContent).toBe("")
    rendered.rerender(view("one"))
    await waitFor(() =>
      expect(screen.getByTestId("answer").textContent).toBe("Answer for one")
    )
    expect(
      (screen.getByRole("textbox", { name: "Draft" }) as HTMLTextAreaElement)
        .value
    ).toBe("First draft")
  })
  it("separates cloud and local threads with the same backend id", async () => {
    vi.stubGlobal("openSweDesktop", {
      getLocalThread: async (id: string) => ({ id, title: id }),
    })
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    })
    const view = (transport: "cloud" | "local") => (
      <QueryClientProvider client={client}>
        <ExperimentalRuntimeProvider
          threadId="same"
          transport={transport}
          cloudEnabled
        >
          <Probe />
        </ExperimentalRuntimeProvider>
      </QueryClientProvider>
    )
    const rendered = render(view("cloud"))
    await waitFor(() =>
      expect(screen.getByTestId("identity").textContent).toBe("same")
    )
    fireEvent.change(screen.getByRole("textbox"), {
      target: { value: "Cloud draft" },
    })
    rendered.rerender(view("local"))
    await waitFor(() => expect(mocks.controllers.has("local:same")).toBe(true))
    await waitFor(() =>
      expect((screen.getByRole("textbox") as HTMLTextAreaElement).value).toBe(
        ""
      )
    )
    act(() => mocks.controllers.get("cloud:same")!.complete())
    expect(screen.getByTestId("answer").textContent).toBe("")
    rendered.rerender(view("cloud"))
    await waitFor(() =>
      expect(screen.getByTestId("answer").textContent).toBe("Answer for same")
    )
    expect((screen.getByRole("textbox") as HTMLTextAreaElement).value).toBe(
      "Cloud draft"
    )
  })

  it("announces an initialized draft only after acceptance and never after switching away", async () => {
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    })
    const onCreated = vi.fn()
    const view = (id: string | null) => (
      <QueryClientProvider client={client}>
        <ExperimentalRuntimeProvider
          threadId={id}
          cloudEnabled
          onThreadCreated={onCreated}
        >
          <Probe />
        </ExperimentalRuntimeProvider>
      </QueryClientProvider>
    )
    const rendered = render(view(null))
    await waitFor(() =>
      expect(screen.getByTestId("identity").textContent).toBe("new")
    )
    fireEvent.click(screen.getByText("Start thread"))
    await waitFor(() =>
      expect(screen.getByTestId("identity").textContent).not.toBe("new")
    )
    const id = screen.getByTestId("identity").textContent!
    expect(onCreated).not.toHaveBeenCalled()
    act(() => mocks.controllers.get(`cloud:${id}`)!.start())
    expect(onCreated).toHaveBeenCalledExactlyOnceWith(id)
    rendered.rerender(view(id))
    await waitFor(() =>
      expect(screen.getByTestId("identity").textContent).toBe(id)
    )
    rendered.rerender(view(null))
    await waitFor(() =>
      expect(screen.getByTestId("identity").textContent).toBe("new")
    )
    fireEvent.click(screen.getByText("Start thread"))
    await waitFor(() =>
      expect(screen.getByTestId("identity").textContent).not.toBe("new")
    )
    const backgroundId = screen.getByTestId("identity").textContent!
    let release = () => {}
    mocks.fetchGate = new Promise<void>((resolve) => {
      release = resolve
    })
    rendered.rerender(view("other"))
    await waitFor(() => expect(screen.queryByTestId("identity")).toBeNull())
    act(() => mocks.controllers.get(`cloud:${backgroundId}`)!.start())
    expect(onCreated).toHaveBeenCalledTimes(1)
    await act(async () => {
      release()
    })
    await waitFor(() =>
      expect(screen.getByTestId("identity").textContent).toBe("other")
    )
  })
  it("shows a load error when the requested thread is unavailable", async () => {
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    })
    render(
      <QueryClientProvider client={client}>
        <ExperimentalRuntimeProvider threadId="missing" cloudEnabled>
          <Probe />
        </ExperimentalRuntimeProvider>
      </QueryClientProvider>
    )
    expect((await screen.findByRole("alert")).textContent).toContain(
      "Could not load this conversation"
    )
    expect(screen.queryByTestId("identity")).toBeNull()
  })
})
