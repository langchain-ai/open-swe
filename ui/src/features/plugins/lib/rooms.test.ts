import type { RoomEvent } from "@openswe/plugin-sdk"
import { afterEach, expect, it, vi } from "vitest"

import { PluginRooms } from "@/features/plugins/lib/rooms"

class FakeEventSource {
  static last: FakeEventSource | null = null
  readonly listeners = new Map<
    string,
    (message: MessageEvent<string>) => void
  >()
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
