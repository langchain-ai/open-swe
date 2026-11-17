/** @vitest-environment jsdom */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

const STORAGE_KEY = "agent-message-timestamps-v1"

/** A fresh copy of the module, as a page load would get. */
async function freshModule() {
  vi.resetModules()
  return import("./messageTimestamps")
}

function stored(): Array<[string, string]> {
  return JSON.parse(window.localStorage.getItem(STORAGE_KEY) ?? "[]")
}

beforeEach(() => {
  window.localStorage.clear()
  vi.useFakeTimers({ now: new Date("2026-11-17T10:00:00.000Z") })
})

afterEach(() => {
  // Earlier modules keep their `pagehide` listeners; leave none with a write
  // pending that a later test's event would trigger.
  vi.runAllTimers()
  vi.useRealTimers()
})

describe("messageArrivalTimestamp", () => {
  it("keeps the stamp a message was first seen with", async () => {
    const { messageArrivalTimestamp } = await freshModule()
    const first = messageArrivalTimestamp("msg-a")
    vi.advanceTimersByTime(60_000)

    expect(first).toBe("2026-11-17T10:00:00.000Z")
    expect(messageArrivalTimestamp("msg-a")).toBe(first)
  })

  it("shows stamps stored by an earlier page", async () => {
    window.localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify([["msg-old", "2026-01-02T03:04:05.000Z"]])
    )
    const { messageArrivalTimestamp } = await freshModule()

    expect(messageArrivalTimestamp("msg-old")).toBe("2026-01-02T03:04:05.000Z")
  })

  it("writes a thread's new stamps once, not once per message", async () => {
    window.localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify(
        Array.from({ length: 4000 }, (_, i) => [
          `old-${i}`,
          "2026-01-01T00:00:00.000Z",
        ])
      )
    )
    const { messageArrivalTimestamp } = await freshModule()
    const setItem = vi.spyOn(Storage.prototype, "setItem")

    for (let i = 0; i < 700; i++) messageArrivalTimestamp(`tool-${i}`)
    expect(setItem).not.toHaveBeenCalled()

    vi.runAllTimers()
    expect(setItem).toHaveBeenCalledTimes(1)
    expect(stored()).toHaveLength(4700)
    setItem.mockRestore()
  })

  it("survives a reload that comes before the write", async () => {
    const first = await freshModule()
    const stamp = first.messageArrivalTimestamp("msg-a")
    window.dispatchEvent(new Event("pagehide"))

    vi.advanceTimersByTime(60_000)
    const reloaded = await freshModule()
    expect(reloaded.messageArrivalTimestamp("msg-a")).toBe(stamp)
  })

  it("keeps stamps another tab wrote, and its own for ids both have", async () => {
    const { messageArrivalTimestamp } = await freshModule()
    const mine = messageArrivalTimestamp("msg-mine")
    const otherTab = JSON.stringify([
      ["msg-mine", "2026-11-17T10:00:05.000Z"],
      ["msg-theirs", "2026-11-17T09:59:00.000Z"],
    ])
    window.localStorage.setItem(STORAGE_KEY, otherTab)
    window.dispatchEvent(
      new StorageEvent("storage", { key: STORAGE_KEY, newValue: otherTab })
    )

    expect(messageArrivalTimestamp("msg-theirs")).toBe(
      "2026-11-17T09:59:00.000Z"
    )
    expect(messageArrivalTimestamp("msg-mine")).toBe(mine)
    vi.runAllTimers()
    expect(new Map(stored())).toEqual(
      new Map([
        ["msg-mine", mine],
        ["msg-theirs", "2026-11-17T09:59:00.000Z"],
      ])
    )
  })

  it("keeps its own newer stamps when another tab writes older ones", async () => {
    const { messageArrivalTimestamp } = await freshModule()
    const mine = Array.from({ length: 5000 }, (_, i) => {
      const id = `mine-${i}`
      return [id, messageArrivalTimestamp(id)]
    })
    const otherTab = JSON.stringify(
      Array.from({ length: 5000 }, (_, i) => [
        `theirs-${i}`,
        "2026-11-17T09:00:00.000Z",
      ])
    )
    window.localStorage.setItem(STORAGE_KEY, otherTab)
    window.dispatchEvent(
      new StorageEvent("storage", { key: STORAGE_KEY, newValue: otherTab })
    )

    // A dropped stamp would be made again, at this later time.
    vi.setSystemTime(new Date("2026-11-17T10:05:00.000Z"))
    expect(messageArrivalTimestamp("mine-0")).toBe(mine[0]![1])
    vi.runAllTimers()
    expect(stored()).toEqual(mine)
  })

  it("keeps the newest 5000 stamps, in memory and in storage", async () => {
    const { messageArrivalTimestamp } = await freshModule()
    const oldest = messageArrivalTimestamp("id-0")
    for (let i = 1; i <= 5000; i++) messageArrivalTimestamp(`id-${i}`)
    vi.runAllTimers()

    const ids = stored().map(([id]) => id)
    expect(ids).toHaveLength(5000)
    expect(ids[0]).toBe("id-1")
    expect(ids.at(-1)).toBe("id-5000")

    vi.advanceTimersByTime(60_000)
    expect(messageArrivalTimestamp("id-0")).not.toBe(oldest)
  })
})
