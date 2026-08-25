import { describe, expect, it } from "vitest"

import { reviewImageProxyUrl } from "./api"

describe("reviewImageProxyUrl", () => {
  it("proxies github user-attachment images", () => {
    const out = reviewImageProxyUrl(
      "acme",
      "repo",
      7,
      "https://github.com/user-attachments/assets/abc-123"
    )
    expect(out).toContain("/dashboard/api/reviews/acme/repo/7/image?url=")
    expect(out).toContain(
      encodeURIComponent("https://github.com/user-attachments/assets/abc-123")
    )
  })

  it("proxies githubusercontent images", () => {
    const out = reviewImageProxyUrl(
      "acme",
      "repo",
      7,
      "https://private-user-images.githubusercontent.com/1/x.png?jwt=y"
    )
    expect(out).toContain("/dashboard/api/reviews/acme/repo/7/image?url=")
  })

  it("leaves non-github image hosts untouched", () => {
    const src = "https://cdn.example.com/x.png"
    expect(reviewImageProxyUrl("acme", "repo", 7, src)).toBe(src)
  })

  it("leaves non-attachment github.com urls untouched", () => {
    const src = "https://github.com/acme/repo/blob/main/x.png"
    expect(reviewImageProxyUrl("acme", "repo", 7, src)).toBe(src)
  })

  it("leaves unparseable urls untouched", () => {
    expect(reviewImageProxyUrl("acme", "repo", 7, "not a url")).toBe(
      "not a url"
    )
  })
})
