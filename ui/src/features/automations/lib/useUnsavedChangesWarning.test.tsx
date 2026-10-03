/** @vitest-environment jsdom */

import {
  act,
  cleanup,
  fireEvent,
  renderHook,
  screen,
} from "@testing-library/react"
import { afterEach, describe, expect, it, vi } from "vitest"

import { useUnsavedChangesWarning } from "./useUnsavedChangesWarning"
import { ConfirmProvider } from "@/components/ConfirmDialog"

const { useBlockerMock } = vi.hoisted(() => ({
  useBlockerMock: vi.fn(),
}))

vi.mock("@tanstack/react-router", () => ({
  useBlocker: useBlockerMock,
}))

type BlockerOptions = { shouldBlockFn: () => Promise<boolean> }

function renderWarning(isDirty: boolean) {
  const hook = renderHook(() => useUnsavedChangesWarning(isDirty), {
    wrapper: ConfirmProvider,
  })
  const options = useBlockerMock.mock.lastCall?.[0] as BlockerOptions
  return { ...hook, options }
}

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe("useUnsavedChangesWarning", () => {
  it("only blocks navigation and unloads while changes are dirty", () => {
    const { rerender } = renderHook(
      ({ isDirty }) => useUnsavedChangesWarning(isDirty),
      { initialProps: { isDirty: false }, wrapper: ConfirmProvider }
    )

    expect(useBlockerMock).toHaveBeenLastCalledWith(
      expect.objectContaining({ disabled: true, enableBeforeUnload: false })
    )

    rerender({ isDirty: true })

    expect(useBlockerMock).toHaveBeenLastCalledWith(
      expect.objectContaining({ disabled: false, enableBeforeUnload: true })
    )
  })

  it("blocks client navigation when the user keeps editing", async () => {
    const { options } = renderWarning(true)

    let pending!: Promise<boolean>
    act(() => {
      pending = options.shouldBlockFn()
    })
    expect(await screen.findByText("Leave without saving?")).toBeTruthy()
    fireEvent.click(screen.getByRole("button", { name: "Keep editing" }))

    await expect(pending).resolves.toBe(true)
  })

  it("allows confirmed and successful navigation", async () => {
    const { options, result } = renderWarning(true)

    let pending!: Promise<boolean>
    act(() => {
      pending = options.shouldBlockFn()
    })
    fireEvent.click(await screen.findByRole("button", { name: "Leave" }))
    await expect(pending).resolves.toBe(false)

    act(() => result.current())

    await expect(options.shouldBlockFn()).resolves.toBe(false)
  })
})
