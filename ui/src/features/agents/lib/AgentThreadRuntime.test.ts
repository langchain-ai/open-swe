import { afterEach, expect, it, vi } from "vitest"
import { AIMessage, HumanMessage } from "@langchain/core/messages"
import { Client } from "@langchain/langgraph-sdk"
import { AgentThreadRuntime } from "./AgentThreadRuntime"

const tick = () => new Promise((resolve) => setTimeout(resolve, 20))
const full = () => [
  new HumanMessage({ id: "u1", content: "first prompt" }),
  new AIMessage({ id: "a1", content: "complete answer" }),
]
const runtimes: AgentThreadRuntime[] = []
afterEach(async () => {
  for (const runtime of runtimes.splice(0)) runtime.dispose()
  await tick()
  vi.restoreAllMocks()
})

async function setup(busy = true, transport: "cloud" | "local" = "cloud") {
  const client = new Client<Record<string, unknown>>({
    apiUrl: "http://runtime.invalid",
    apiKey: null,
  })
  let state = {
    values: { messages: full() },
    next: busy ? ["model"] : [],
    tasks: [],
    metadata: { step: 8 },
    checkpoint: { checkpoint_id: "cp8" },
    thread_status: busy ? "running" : "idle",
  }
  let stateBarrier: Promise<void> | undefined
  vi.spyOn(client.threads, "getState").mockImplementation(async () => {
    const snapshot = state
    await stateBarrier
    return snapshot as never
  })
  vi.spyOn(client.threads, "get").mockImplementation(
    async () =>
      ({ status: state.thread_status === "running" ? "busy" : "idle" }) as never
  )
  vi.spyOn(client.threads, "getHistory").mockResolvedValue([])
  vi.spyOn(client.runs, "list").mockResolvedValue([])
  const roots = new Set<ReadableStreamDefaultController<Uint8Array>>()
  let commandError = false
  let finite = false
  const fetchMock = vi.fn<typeof fetch>(async (_url, init) => {
    const request = JSON.parse(init?.body as string)
    if (request.method)
      return Response.json(
        commandError
          ? {
              type: "error",
              id: request.id,
              error: { code: "FAILED", message: "Run rejected" },
            }
          : { type: "success", id: request.id, result: { run_id: "run-1" } }
      )
    if (finite)
      return new Response("", {
        headers: { "content-type": "text/event-stream" },
      })
    return new Response(
      new ReadableStream({
        start(controller) {
          if (request.channels.includes("messages")) roots.add(controller)
          init?.signal?.addEventListener(
            "abort",
            () => {
              roots.delete(controller)
              if (controller.desiredSize !== null) {
                try {
                  controller.close()
                } catch {}
              }
            },
            { once: true }
          )
        },
      }),
      { headers: { "content-type": "text/event-stream" } }
    )
  })
  const runtime = new AgentThreadRuntime({
    client,
    threadId: "thread-1",
    transport,
    fetch: fetchMock,
    onCreated: vi.fn(),
    onThreadId: vi.fn(),
    onCompleted: vi.fn(),
  })
  runtimes.push(runtime)
  await runtime.controller.hydrationPromise
  await tick()
  let seq = 0
  return {
    runtime,
    client,
    fetchMock,
    failCommand: () => {
      commandError = true
    },
    closeStreams: () => {
      finite = true
      for (const root of roots) root.close()
      roots.clear()
    },
    setState: (next: Partial<typeof state>) => {
      state = { ...state, ...next }
    },
    deferState: () => {
      let release!: () => void
      stateBarrier = new Promise<void>((resolve) => {
        release = resolve
      })
      return () => {
        stateBarrier = undefined
        release()
      }
    },
    emit: async (method: string, data: object) => {
      const event = {
        type: "event",
        method,
        seq: ++seq,
        event_id: `event-${seq}`,
        params: { namespace: [], node: "model", data },
      }
      for (const root of roots)
        root.enqueue(
          new TextEncoder().encode(`data: ${JSON.stringify(event)}\n\n`)
        )
      await tick()
    },
  }
}

it("keeps hydrated history through no-step values replay and message restart", async () => {
  const { runtime, emit } = await setup()
  await emit("values", { messages: full().slice(0, 1) })
  await emit("messages", { event: "message-start", id: "a1", role: "ai" })
  await emit("messages", {
    event: "content-block-delta",
    index: 0,
    delta: { type: "text-delta", text: "complete" },
  })
  expect(runtime.store.getSnapshot().messages.map((m) => m.text)).toEqual([
    "first prompt",
    "complete answer",
  ])
})

it("repairs truncated live output from a final checkpoint and clears stale busy", async () => {
  const { runtime, emit, setState } = await setup()
  await emit("lifecycle", { event: "running", run_id: "run-1" })
  await emit("messages", { event: "message-start", id: "a2", role: "ai" })
  await emit("messages", {
    event: "content-block-delta",
    index: 0,
    delta: { type: "text-delta", text: "partial" },
  })
  expect(runtime.store.getSnapshot().messages.at(-1)?.text).toBe("partial")
  await emit("messages", { event: "message-start", id: "a2", role: "ai" })
  expect(runtime.store.getSnapshot().messages.at(-1)?.text).toBe("partial")
  setState({
    values: {
      messages: [
        ...full(),
        new AIMessage({ id: "a2", content: "final corrected answer" }),
      ],
    },
    metadata: { step: 9 },
    next: [],
    thread_status: "idle",
  })
  await runtime.refresh()
  expect(runtime.store.getSnapshot().messages.at(-1)?.text).toBe(
    "final corrected answer"
  )
  expect(runtime.store.getSnapshot().isLoading).toBe(false)
})

it("detects an externally started run while the page is idle", async () => {
  const { runtime, setState, fetchMock, emit } = await setup(false)
  expect(fetchMock).not.toHaveBeenCalled()
  setState({ thread_status: "running", next: [] })
  await runtime.refresh()
  expect(runtime.store.getSnapshot().isLoading).toBe(true)
  await emit("messages", { event: "message-start", id: "external", role: "ai" })
  await emit("messages", {
    event: "content-block-delta",
    index: 0,
    delta: { type: "text-delta", text: "External reply" },
  })
  expect(runtime.store.getSnapshot().messages.at(-1)?.text).toBe(
    "External reply"
  )
})

it("accepts a send before the run ends and rejects a failed dispatch", async () => {
  const { runtime } = await setup(false)
  await runtime.submit({ messages: [{ type: "human", content: "hello" }] })
  expect(runtime.store.getSnapshot().isLoading).toBe(true)
  const failed = await setup(false)
  failed.failCommand()
  await expect(
    failed.runtime.submit({
      messages: [{ type: "human", content: "keep this draft" }],
    })
  ).rejects.toThrow("The message was not accepted. Please retry.")
  expect(
    failed.runtime.store.getSnapshot().messages.map((m) => m.text)
  ).not.toContain("keep this draft")
})

it("does not let a delayed idle poll disconnect a newly accepted run", async () => {
  const { runtime, deferState } = await setup(false)
  const disconnect = vi.spyOn(runtime.controller, "disconnect")
  const release = deferState()
  const refresh = runtime.refresh()
  await runtime.submit({ messages: [{ type: "human", content: "new run" }] })
  release()
  await refresh
  expect(runtime.store.getSnapshot().isLoading).toBe(true)
  expect(disconnect).not.toHaveBeenCalled()
})

it("recovers status on clean EOF without cancelling the server run", async () => {
  const { runtime, setState, closeStreams, client } = await setup()
  const cancel = vi.spyOn(client.runs, "cancel")
  setState({ thread_status: "idle", next: [] })
  closeStreams()
  await tick()
  await runtime.refresh()
  expect(runtime.store.getSnapshot().isLoading).toBe(false)
  expect(runtime.store.getSnapshot().messages.map((m) => m.text)).toEqual([
    "first prompt",
    "complete answer",
  ])
  expect(cancel).not.toHaveBeenCalled()
})

it("recovers queued local runs on a fresh runtime and cancels them before the active run", async () => {
  const { runtime, client } = await setup(true, "local")
  const queued = { id: "q", content: "next prompt", createdAt: Date.now() }
  vi.mocked(client.runs.list).mockResolvedValueOnce([
    {
      run_id: "q-run",
      status: "pending",
      metadata: { dashboard_queued_message: queued },
    },
  ] as never)
  await runtime.refresh()
  expect(runtime.store.getSnapshot().queuedMessages).toEqual([queued])
  vi.mocked(client.runs.list).mockResolvedValueOnce([
    { run_id: "active", status: "running" },
    { run_id: "queued", status: "pending" },
  ] as never)
  const cancel = vi.spyOn(client.runs, "cancel").mockResolvedValue(undefined)
  await runtime.stop()
  expect(cancel.mock.calls.map((call) => call[1])).toEqual(["queued", "active"])
})
