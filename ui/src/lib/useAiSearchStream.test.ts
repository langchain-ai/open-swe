/** @vitest-environment jsdom */

import { act, renderHook } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

import { useAiSearchStream } from "./useAiSearchStream"

class FakeEventSource {
  static last: FakeEventSource | null = null
  closed = false
  private listeners = new Map<string, Array<(event: MessageEvent) => void>>()

  constructor(readonly url: string) {
    FakeEventSource.last = this
  }

  addEventListener(type: string, listener: (event: MessageEvent) => void) {
    this.listeners.set(type, [...(this.listeners.get(type) ?? []), listener])
  }

  close() {
    this.closed = true
  }

  emit(type: string, data: object = {}) {
    for (const listener of this.listeners.get(type) ?? []) {
      listener(new MessageEvent(type, { data: JSON.stringify(data) }))
    }
  }
}

vi.stubGlobal("EventSource", FakeEventSource)

afterEach(() => {
  FakeEventSource.last = null
})

const key = (match: { id: string }) => match.id

describe("useAiSearchStream", () => {
  it("re-ranks as older batches arrive and closes when the search ends", () => {
    const { result } = renderHook(() =>
      useAiSearchStream<{ id: string; relevance: number }>(
        "/threads/search?q=dm",
        key
      )
    )
    const source = FakeEventSource.last
    expect(source?.url).toContain("/dashboard/api/threads/search?q=dm")
    expect(result.current.status).toBe("streaming")

    act(() =>
      source?.emit("batch", {
        matches: [{ id: "recent", relevance: 0.6 }],
        scanned: 10,
      })
    )
    act(() =>
      source?.emit("batch", {
        matches: [{ id: "older", relevance: 0.9 }],
        scanned: 20,
      })
    )
    expect(result.current.matches.map((match) => match.id)).toEqual([
      "older",
      "recent",
    ])
    expect(result.current.scanned).toBe(20)

    act(() => source?.emit("done", { scanned: 20 }))
    expect(result.current.status).toBe("done")
    expect(source?.closed).toBe(true)
  })

  it("closes the stream when the query goes away", () => {
    const { rerender, result } = renderHook(
      ({ path }: { path: string | null }) =>
        useAiSearchStream<{ id: string; relevance: number }>(path, key),
      { initialProps: { path: "/threads/search?q=dm" as string | null } }
    )
    const source = FakeEventSource.last
    rerender({ path: null })
    expect(source?.closed).toBe(true)
    expect(result.current.status).toBe("idle")
  })
})
