// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from "vitest"

import { executeVoiceTool, VoiceManager } from "./voice"

const fetchMock = vi.fn<typeof fetch>()
vi.stubGlobal("fetch", fetchMock)

afterEach(() => {
  fetchMock.mockReset()
  vi.unstubAllGlobals()
  vi.stubGlobal("fetch", fetchMock)
})

function response(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  })
}

describe("executeVoiceTool", () => {
  it("returns the actual thread transcript from state", async () => {
    fetchMock
      .mockResolvedValueOnce(response({ id: "thread-1", title: "Voice" }))
      .mockResolvedValueOnce(
        response({
          values: {
            messages: [
              { type: "human", content: "Please fix it" },
              {
                type: "ai",
                content: [{ type: "text", text: "Working on it" }],
              },
            ],
          },
        })
      )

    await expect(
      executeVoiceTool("get_thread", '{"thread_id":"thread-1"}', vi.fn())
    ).resolves.toMatchObject({
      thread: { id: "thread-1" },
      transcript: [
        { role: "human", content: "Please fix it" },
        { role: "ai", content: "Working on it" },
      ],
    })
    expect(fetchMock.mock.calls.map(([url]) => String(url))).toEqual([
      "/dashboard/api/threads/thread-1?mark_viewed=false",
      "/dashboard/api/threads/thread-1/state",
    ])
  })

  it("lists searched pages using the dashboard response shape", async () => {
    fetchMock.mockResolvedValueOnce(
      response({
        items: [{ id: "thread-26", title: "Older result", status: "finished" }],
        offset: 25,
        hasMore: true,
      })
    )

    await expect(
      executeVoiceTool(
        "list_threads",
        { query: "older result", offset: 25 },
        vi.fn()
      )
    ).resolves.toEqual({
      threads: [{ id: "thread-26", title: "Older result", status: "finished" }],
      offset: 25,
      has_more: true,
    })
    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      "/dashboard/api/threads/page?limit=25&offset=25&scope=interactive&sort_by=updated_at&q=older+result"
    )
  })

  it("creates a thread with the supported run.start command", async () => {
    vi.stubGlobal("crypto", { randomUUID: () => "thread-new" })
    fetchMock.mockResolvedValueOnce(response({ type: "success" }))

    await expect(
      executeVoiceTool("create_thread", { message: "new task" }, vi.fn())
    ).resolves.toEqual({ status: "started", thread_id: "thread-new" })
    expect(fetchMock).toHaveBeenCalledWith(
      "/dashboard/api/threads/thread-new/commands",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({
          method: "run.start",
          params: {
            input: { messages: [{ role: "user", content: "new task" }] },
          },
        }),
      })
    )
  })

  it("queues a message when the thread is running", async () => {
    fetchMock
      .mockResolvedValueOnce(response({ id: "thread-1", status: "running" }))
      .mockResolvedValueOnce(response({ id: "thread-1" }))

    await expect(
      executeVoiceTool(
        "send_message",
        { thread_id: "thread-1", message: "next task" },
        vi.fn()
      )
    ).resolves.toEqual({ status: "queued", thread_id: "thread-1" })
    expect(fetchMock.mock.calls[1]?.[1]).toMatchObject({
      method: "POST",
      body: JSON.stringify({ content: "next task" }),
      credentials: "include",
    })
  })

  it("rejects malformed and excess arguments before making a request", async () => {
    await expect(
      executeVoiceTool(
        "stop_thread",
        { thread_id: "thread-1", message: "not allowed" },
        vi.fn()
      )
    ).rejects.toThrow("Invalid tool arguments")
    await expect(
      executeVoiceTool("create_thread", { message: " " }, vi.fn())
    ).rejects.toThrow("Invalid message")
    await expect(
      executeVoiceTool(
        "list_threads",
        { query: "x".repeat(501), offset: 0 },
        vi.fn()
      )
    ).rejects.toThrow("Invalid list_threads arguments")
    expect(fetchMock).not.toHaveBeenCalled()
  })
})

describe("VoiceManager", () => {
  function event(manager: VoiceManager, value: unknown) {
    ;(
      manager as unknown as {
        onEvent: (raw: string, generation: number) => void
      }
    ).onEvent(JSON.stringify(value), 0)
  }

  function channel() {
    return {
      readyState: "open",
      send: vi.fn(),
      close: vi.fn(),
      onmessage: null,
    } as unknown as RTCDataChannel
  }

  it("returns tool results and continues only after response.completed", async () => {
    fetchMock.mockResolvedValueOnce(
      response({ items: [{ id: "thread-1", title: "One" }], hasMore: false })
    )
    const manager = new VoiceManager(vi.fn())
    const dataChannel = channel()
    ;(manager as unknown as { channel: RTCDataChannel }).channel = dataChannel

    event(manager, {
      type: "response.event",
      event: { type: "response.created", response: { id: "response-1" } },
    })
    event(manager, {
      type: "response.event",
      event: {
        type: "response.output_item.done",
        response_id: "response-1",
        item: {
          type: "function_call",
          call_id: "call-1",
          name: "list_threads",
          arguments: '{"query":"","offset":0}',
        },
      },
    })
    await Promise.resolve()
    expect(dataChannel.send).not.toHaveBeenCalled()

    event(manager, {
      type: "response.event",
      event: {
        type: "response.completed",
        response: { id: "response-1", output: [] },
      },
    })
    await vi.waitFor(() => expect(dataChannel.send).toHaveBeenCalledTimes(2))
    expect(dataChannel.send).toHaveBeenNthCalledWith(
      1,
      expect.stringContaining('"type":"response.item.create"')
    )
    expect(dataChannel.send).toHaveBeenNthCalledWith(
      2,
      JSON.stringify({ type: "response.create" })
    )
  })

  it("stops the microphone and invalidates queued tools before graceful close", async () => {
    let resolveFetch: (value: Response) => void = () => undefined
    fetchMock.mockReturnValueOnce(
      new Promise<Response>((resolve) => {
        resolveFetch = resolve
      })
    )
    const manager = new VoiceManager(vi.fn())
    const dataChannel = channel()
    const stopTrack = vi.fn()
    ;(manager as unknown as { channel: RTCDataChannel }).channel = dataChannel
    ;(manager as unknown as { stream: MediaStream }).stream = {
      getTracks: () => [{ stop: stopTrack }],
    } as unknown as MediaStream
    event(manager, { type: "session.started" })
    event(manager, {
      type: "response.event",
      event: { type: "response.created", response: { id: "response-1" } },
    })
    for (const callId of ["call-1", "call-2"]) {
      event(manager, {
        type: "response.event",
        event: {
          type: "response.output_item.done",
          response_id: "response-1",
          item: {
            type: "function_call",
            call_id: callId,
            name: "list_threads",
            arguments: '{"query":"","offset":0}',
          },
        },
      })
    }
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledOnce())

    manager.stop()
    expect(stopTrack).toHaveBeenCalledOnce()
    expect(manager.getSnapshot().status).toBe("stopping")
    resolveFetch(response({ items: [] }))
    await Promise.resolve()
    await Promise.resolve()

    expect(fetchMock).toHaveBeenCalledOnce()
    expect(dataChannel.send).toHaveBeenCalledOnce()
    expect(dataChannel.send).toHaveBeenCalledWith(
      JSON.stringify({ type: "session.close" })
    )
  })

  it("stops a late microphone stream after cancellation", async () => {
    let grantPermission: (stream: MediaStream) => void = () => undefined
    const getUserMedia = vi.fn(
      () =>
        new Promise<MediaStream>((resolve) => {
          grantPermission = resolve
        })
    )
    Object.defineProperty(navigator, "mediaDevices", {
      configurable: true,
      value: { getUserMedia },
    })
    const stop = vi.fn()
    const stream = { getTracks: () => [{ stop }] } as unknown as MediaStream
    const manager = new VoiceManager(vi.fn())

    const starting = manager.start()
    manager.stop()
    grantPermission(stream)
    await starting

    expect(stop).toHaveBeenCalledOnce()
    expect(manager.getSnapshot().status).toBe("idle")
    expect(fetchMock).not.toHaveBeenCalled()
  })
})
