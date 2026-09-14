import { describe, expect, it } from "vitest"

import { isCrossOriginApiBase, resolveDashboardApiBase } from "./api-base"

describe("resolveDashboardApiBase", () => {
  it("uses the configured API for the web UI", () => {
    expect(resolveDashboardApiBase(undefined, "https:")).toBe("")
    expect(resolveDashboardApiBase("", "https:", "/open-swe/")).toBe(
      "/open-swe"
    )
    expect(
      resolveDashboardApiBase(
        "https://backend.example/",
        "https:",
        "/open-swe/"
      )
    ).toBe("https://backend.example")
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

describe("isCrossOriginApiBase", () => {
  it("treats an unset base as same-origin", () => {
    expect(isCrossOriginApiBase(undefined, "https://dash.example")).toBe(false)
    expect(isCrossOriginApiBase("", "https://dash.example")).toBe(false)
  })

  it("treats a base on the dashboard's own origin as same-origin", () => {
    expect(
      isCrossOriginApiBase("https://dash.example/", "https://dash.example")
    ).toBe(false)
  })

  it("detects a backend on another origin", () => {
    expect(
      isCrossOriginApiBase("https://api.example", "https://dash.example")
    ).toBe(true)
  })

  it("detects a backend on another port", () => {
    expect(
      isCrossOriginApiBase("http://127.0.0.1:2024", "http://127.0.0.1:3000")
    ).toBe(true)
  })

  it("treats an unparseable base as same-origin rather than skipping the gate", () => {
    expect(isCrossOriginApiBase("not a url", "https://dash.example")).toBe(
      false
    )
  })
})
