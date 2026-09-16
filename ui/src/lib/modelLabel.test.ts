import { describe, expect, it } from "vitest"

import { safeModelLabel } from "./modelLabel"

describe("safeModelLabel", () => {
  it("keeps the final path component and sanitizes it", () => {
    expect(
      safeModelLabel("fireworks:accounts/fireworks/models/glm-5p3 flash")
    ).toBe("glm-5p3-flash")
  })

  it("limits labels to 48 characters and trims edge hyphens", () => {
    expect(safeModelLabel("///" + "a".repeat(60))).toBe("a".repeat(48))
    expect(safeModelLabel("///---model---")).toBe("model")
  })
})
