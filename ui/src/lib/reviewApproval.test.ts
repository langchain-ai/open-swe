import { describe, expect, it } from "vitest"

import { parseApprovalThreshold } from "./reviewApproval"

describe("parseApprovalThreshold", () => {
  it("does not coerce an empty draft to zero", () => {
    expect(parseApprovalThreshold("")).toBeNull()
    expect(parseApprovalThreshold("   ")).toBeNull()
  })

  it("accepts only integer thresholds from 0 to 100", () => {
    expect(parseApprovalThreshold("0")).toBe(0)
    expect(parseApprovalThreshold("100")).toBe(100)
    expect(parseApprovalThreshold("1.5")).toBeNull()
    expect(parseApprovalThreshold("101")).toBeNull()
  })
})
