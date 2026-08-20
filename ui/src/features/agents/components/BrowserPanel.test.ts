import { describe, expect, it } from "vitest"

import { normalizePreviewUrl } from "./BrowserPanel"

describe("normalizePreviewUrl", () => {
  it("accepts web addresses and supplies a local-development scheme", () => {
    expect(normalizePreviewUrl("localhost:3000")).toBe("http://localhost:3000/")
    expect(normalizePreviewUrl("https://example.com/path")).toBe(
      "https://example.com/path"
    )
  })

  it("rejects non-web and invalid addresses", () => {
    expect(normalizePreviewUrl("javascript:alert(1)")).toBeNull()
    expect(normalizePreviewUrl("file:///etc/passwd")).toBeNull()
    expect(normalizePreviewUrl("http://")).toBeNull()
  })
})
