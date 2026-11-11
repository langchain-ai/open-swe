/** @vitest-environment jsdom */

import { act, cleanup, renderHook } from "@testing-library/react"
import { afterEach, beforeEach, expect, it, vi } from "vitest"

import { useThreadListLoad } from "./threadListLoad"
import { clearPerfSpans, getPerfSpans } from "./trace"

beforeEach(() => vi.useFakeTimers())
afterEach(() => {
  cleanup()
  clearPerfSpans()
  vi.useRealTimers()
})

it("measures list readiness through paint without counting background refreshes", () => {
  const { rerender } = renderHook(
    ({ pending, items }) =>
      useThreadListLoad({
        scope: "recents",
        identity: "recents",
        enabled: true,
        pending,
        failed: false,
        items,
      }),
    { initialProps: { pending: true, items: 0 } }
  )
  act(() => vi.advanceTimersByTime(100))
  expect(getPerfSpans()[0]?.status).toBe("open")
  rerender({ pending: false, items: 3 })
  expect(getPerfSpans()[0]?.status).toBe("open")
  act(() => vi.advanceTimersByTime(50))
  expect(getPerfSpans()[0]).toMatchObject({
    name: "thread_list_load",
    status: "ended",
    attributes: {
      scope: "recents",
      cached: false,
      items: 3,
      outcome: "success",
    },
  })
  rerender({ pending: false, items: 4 })
  act(() => vi.advanceTimersByTime(50))
  expect(getPerfSpans()).toHaveLength(1)
  expect(getPerfSpans()[0]?.attributes.items).toBe(3)
})

it("tracks cached lists and filter changes without emitting list identifiers", () => {
  const { rerender } = renderHook(
    ({ identity }) =>
      useThreadListLoad({
        scope: "folder",
        identity,
        enabled: true,
        pending: false,
        failed: false,
        items: 0,
      }),
    { initialProps: { identity: "private-project" } }
  )
  act(() => vi.advanceTimersByTime(50))
  rerender({ identity: "another-private-project" })
  act(() => vi.advanceTimersByTime(50))
  expect(getPerfSpans()).toHaveLength(2)
  for (const span of getPerfSpans()) {
    expect(span.status).toBe("ended")
    expect(span.attributes).toEqual({
      scope: "folder",
      cached: true,
      items: 0,
      outcome: "success",
    })
  }
})

it("records failures separately and cancels paints when a folder closes", () => {
  const { rerender } = renderHook(
    ({ enabled, failed }) =>
      useThreadListLoad({
        scope: "folder",
        identity: "folder",
        enabled,
        pending: false,
        failed,
        items: 0,
      }),
    { initialProps: { enabled: true, failed: true } }
  )
  act(() => vi.advanceTimersByTime(50))
  expect(getPerfSpans()[0]?.attributes.outcome).toBe("error")
  rerender({ enabled: false, failed: false })
  rerender({ enabled: true, failed: false })
  rerender({ enabled: false, failed: false })
  act(() => vi.advanceTimersByTime(50))
  expect(getPerfSpans()[1]?.status).toBe("abandoned")
})
