/** @vitest-environment jsdom */
import { act, renderHook } from "@testing-library/react"
import { describe, expect, it, vi } from "vitest"

import { useApproval } from "@/features/plugins/lib/approval"

describe("useApproval", () => {
  it("waits the full delay when a dismissed request is replaced", async () => {
    vi.useFakeTimers()
    try {
      const { result, unmount } = renderHook(() => useApproval())
      act(() => {
        void result.current.ask({ kind: "link", url: "https://example.com/a" })
      })
      act(() => vi.advanceTimersByTime(300))
      act(() => result.current.answer(null))
      act(() => {
        void result.current.ask({ kind: "link", url: "https://example.com/b" })
      })
      act(() => vi.advanceTimersByTime(300))
      expect(result.current.armed).toBe(false)
      act(() => vi.advanceTimersByTime(300))
      expect(result.current.armed).toBe(true)
      act(() => result.current.answer(null))
      unmount()
    } finally {
      vi.useRealTimers()
    }
  })
  it("refuses a second request while one is shown, so it cannot be swapped", async () => {
    const { result } = renderHook(() => useApproval())
    let first: Promise<unknown> | undefined
    act(() => {
      first = result.current.ask({ kind: "link", url: "https://example.com/a" })
    })
    await expect(
      result.current.ask({ kind: "link", url: "https://evil.test" })
    ).rejects.toMatchObject({ status: 409 })
    expect(result.current.shown).toEqual({
      kind: "link",
      url: "https://example.com/a",
    })
    act(() => result.current.answer(true))
    await expect(first).resolves.toBe(true)
  })
})
