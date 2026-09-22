import { describe, expect, it } from "vitest"

import { safeModelLabel } from "./modelLabel"

describe("safeModelLabel", () => {
  it("keeps the provider prefix and final path component", () => {
    expect(
      safeModelLabel("fireworks:accounts/fireworks/models/glm-5p3 flash")
    ).toBe("fireworks:glm-5p3-flash")
    expect(safeModelLabel("openai:gpt-6-sol")).toBe("openai:gpt-6-sol")
  })

  it("limits labels to 48 characters and trims edge hyphens", () => {
    expect(safeModelLabel("///" + "a".repeat(60))).toBe("a".repeat(48))
    expect(safeModelLabel("///---model---")).toBe("model")
  })
})
