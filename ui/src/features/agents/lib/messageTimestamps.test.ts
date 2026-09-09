/** @vitest-environment jsdom */
import { afterEach, expect, it, vi } from "vitest"

afterEach(() => {
  vi.useRealTimers()
  vi.restoreAllMocks()
})

it("persists a large hydration batch once and keeps arrival times stable", async () => {
  vi.useFakeTimers()
  const writes = vi.spyOn(Storage.prototype, "setItem")
  const { messageArrivalTimestamp } = await import("./messageTimestamps")
  const first = messageArrivalTimestamp("batch-0")
  for (let i = 1; i < 3000; i++) messageArrivalTimestamp(`batch-${i}`)
  expect(writes).not.toHaveBeenCalled()
  vi.advanceTimersByTime(250)
  expect(writes).toHaveBeenCalledTimes(1)
  expect(JSON.parse(writes.mock.calls[0]![1])).toHaveLength(3000)
  expect(messageArrivalTimestamp("batch-0")).toBe(first)
})
