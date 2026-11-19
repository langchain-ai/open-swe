import { describe, expect, it } from "vitest"

import { Assessment } from "./assessment"
import type { DesignSystemFinding } from "./capture"

const finding = (severity: DesignSystemFinding["severity"]) => ({
  severity,
  title: "Raw hex color",
  body: "",
  rule: "DESIGN.md · Colors",
  location: null,
  scenario: null,
})

describe("Assessment.risk", () => {
  it("reads 1–2 as low, 3 as medium and 4–5 as high", () => {
    expect(Assessment.risk({ score: 2, reason: "" })).toEqual({
      tone: "success",
      label: "Risk 2/5 · Low",
    })
    expect(Assessment.risk({ score: 3, reason: "" }).tone).toBe("warning")
    expect(Assessment.risk({ score: 5, reason: "" }).tone).toBe("error")
  })
})

describe("Assessment.designSystem", () => {
  it("fails on any violation, flags warnings and passes without findings", () => {
    expect(
      Assessment.designSystem({
        sources: [],
        findings: [finding("warning"), finding("error"), finding("error")],
      })
    ).toEqual({
      tone: "error",
      label: "2 design system violations",
      errors: 2,
      warnings: 1,
    })
    expect(
      Assessment.designSystem({ sources: [], findings: [finding("warning")] })
        .label
    ).toBe("1 design system warning")
    expect(Assessment.designSystem({ sources: [], findings: [] }).tone).toBe(
      "success"
    )
  })
})
