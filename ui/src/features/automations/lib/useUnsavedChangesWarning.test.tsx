/** @vitest-environment jsdom */

import { act, cleanup, renderHook } from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

import { useUnsavedChangesWarning } from "./useUnsavedChangesWarning"

const { useBlockerMock } = vi.hoisted(() => ({
  useBlockerMock: vi.fn(),
}))

vi.mock("@tanstack/react-router", () => ({
  useBlocker: useBlockerMock,
}))

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
  vi.restoreAllMocks()
})

describe("useUnsavedChangesWarning", () => {
  it("only blocks navigation and unloads while changes are dirty", () => {
    const { rerender } = renderHook(
      ({ isDirty }) => useUnsavedChangesWarning(isDirty),
      { initialProps: { isDirty: false } }
    )

    expect(useBlockerMock).toHaveBeenLastCalledWith(
      expect.objectContaining({ disabled: true, enableBeforeUnload: false })
    )

    rerender({ isDirty: true })

    expect(useBlockerMock).toHaveBeenLastCalledWith(
      expect.objectContaining({ disabled: false, enableBeforeUnload: true })
    )
  })

  it("blocks client navigation when the user keeps editing", () => {
    vi.spyOn(window, "confirm").mockReturnValue(false)
    renderHook(() => useUnsavedChangesWarning(true))
    const options = useBlockerMock.mock.lastCall?.[0] as {
      shouldBlockFn: () => boolean
    }

    expect(options.shouldBlockFn()).toBe(true)
    expect(window.confirm).toHaveBeenCalledWith(
      "You have unsaved changes. Leave without saving?"
    )
  })

  it("allows confirmed and successful navigation", () => {
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true)
    const { result } = renderHook(() => useUnsavedChangesWarning(true))
    const options = useBlockerMock.mock.lastCall?.[0] as {
      shouldBlockFn: () => boolean
    }

    expect(options.shouldBlockFn()).toBe(false)

    confirm.mockClear()
    act(() => result.current())

    expect(options.shouldBlockFn()).toBe(false)
    expect(confirm).not.toHaveBeenCalled()
  })
})
