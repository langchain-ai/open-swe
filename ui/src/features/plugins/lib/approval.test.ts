/** @vitest-environment jsdom */
import { act, renderHook } from "@testing-library/react"
import { describe, expect, it } from "vitest"

import { useApproval } from "@/features/plugins/lib/approval"

describe("useApproval", () => {
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
