import type { RoomEvent } from "@openswe/plugin-sdk"
import { afterEach, expect, it, vi } from "vitest"

import { AgentsApiError } from "@/features/agents/lib/api"
import { pluginsApi } from "@/features/plugins/lib/api"
import { PluginRooms } from "@/features/plugins/lib/rooms"

class FakeEventSource {
  static readonly CLOSED = 2
  static last: FakeEventSource | null = null
  readonly listeners = new Map<
    string,
    (message: MessageEvent<string>) => void
  >()
  readyState = 0
  onerror: (() => void) | null = null
  constructor(readonly url: string) {
    FakeEventSource.last = this
  }
  addEventListener(
    type: string,
    listener: (message: MessageEvent<string>) => void
  ) {
    this.listeners.set(type, listener)
  }
  close() {}
  send(type: string, data: object) {
    this.listeners.get(type)?.({
      data: JSON.stringify(data),
    } as MessageEvent<string>)
  }
}

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

it("delivers a room joined twice to both listeners", () => {
  vi.useFakeTimers()
  vi.stubGlobal("EventSource", FakeEventSource)
  const rooms = new PluginRooms("install-1")
  const first: RoomEvent[] = []
  const second: RoomEvent[] = []
  rooms.join({ room: "draft/a.md" }, 0, (event) => first.push(event))
  rooms.join({ room: "draft/a.md" }, 0, (event) => second.push(event))
  vi.runAllTimers()

  FakeEventSource.last?.send("update", {
    room: "draft/a.md",
    repository: null,
    seq: 1,
    update: "AQI=",
  })

  expect(first).toHaveLength(1)
  expect(second).toHaveLength(1)
  rooms.closeAll()
})

it("tells only the rooms the backend refuses, and keeps streaming the rest", async () => {
  vi.useFakeTimers()
  vi.stubGlobal("EventSource", FakeEventSource)
  vi.spyOn(pluginsApi, "roomsStreamError").mockImplementation(
    async (_id, rooms) =>
      rooms.some((room) => room.repository === "acme/app")
        ? new AgentsApiError(403, "acme/app is not shared with the plugin")
        : null
  )
  const rooms = new PluginRooms("install-1")
  const shared: RoomEvent[] = []
  const mine: RoomEvent[] = []
  rooms.join({ room: "a", repository: "acme/app" }, 0, (event) =>
    shared.push(event)
  )
  rooms.join({ room: "b" }, 0, (event) => mine.push(event))
  vi.runAllTimers()
  const refused = FakeEventSource.last!
  refused.readyState = FakeEventSource.CLOSED

  refused.onerror?.()
  await vi.runAllTimersAsync()

  expect(shared).toEqual([
    {
      type: "error",
      status: 403,
      message: "acme/app is not shared with the plugin",
    },
  ])
  expect(mine).toEqual([])
  const reopened = new URL(FakeEventSource.last!.url, "http://localhost")
  expect(JSON.parse(reopened.searchParams.get("rooms")!)).toEqual([
    { room: "b", repository: null, after: 0 },
  ])
  rooms.closeAll()
})
