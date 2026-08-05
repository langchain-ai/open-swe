import { describe, expect, it } from "vitest"

import { resolveDashboardApiBase } from "./api-base"

describe("resolveDashboardApiBase", () => {
  it("uses the configured API for the web UI", () => {
    expect(resolveDashboardApiBase("https://backend.example/", "https:")).toBe(
      "https://backend.example"
    )
  })

  it("uses the Electron proxy even if the build has a configured API", () => {
    expect(
      resolveDashboardApiBase("https://maintainer.example", "open-swe:")
    ).toBe("")
  })
})
