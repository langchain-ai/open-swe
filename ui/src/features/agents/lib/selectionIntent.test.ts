import { describe, expect, it } from "vitest"
import { createSelectionIntent } from "./selectionIntent"

describe("Model selection intent", () => {
  it("claims only a deliberate picker action and only for an eligible submission", () => {
    const intent = createSelectionIntent()
    expect(intent.claim("inherited-selection", true)).toBe(false)
    intent.select()
    expect(intent.claim("steering-or-offload", false)).toBe(false)
    expect(intent.claim("accepted", true)).toBe(true)
    expect(intent.claim("follow-up", true)).toBe(false)
    intent.select()
    expect(intent.claim("new-picker-action", true)).toBe(true)
  })

  it("restores only the matching submission and ignores late errors after resubmission", () => {
    const intent = createSelectionIntent()
    intent.select()
    expect(intent.claim("withdrawn", true)).toBe(true)
    intent.restore("another-message")
    expect(intent.claim("still-consumed", true)).toBe(false)
    intent.restore("withdrawn")
    expect(intent.claim("resubmitted", true)).toBe(true)
    intent.restore("withdrawn")
    expect(intent.claim("late-callback", true)).toBe(false)
    intent.restore("resubmitted")
    expect(intent.claim("retry", true)).toBe(true)
  })

  it.each(["pending", "consumed"] as const)(
    "does not overwrite a newer %s picker action on withdrawal or failure",
    (selection) => {
      const intent = createSelectionIntent()
      intent.select()
      expect(intent.claim("old-submission", true)).toBe(true)
      intent.select()
      if (selection === "consumed") intent.claim("new-submission", true)
      intent.restore("old-submission")
      expect(intent.claim("next", true)).toBe(selection === "pending")
      expect(intent.claim("follow-up", true)).toBe(false)
    }
  )
})
