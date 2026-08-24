import { describe, expect, it } from "vitest"

import {
  isSameOriginRequest,
  localGraphConfiguration,
  localGraphTarget,
} from "./local-graph-proxy"

describe("local graph proxy", () => {
  it("accepts only an authenticated private loopback graph origin", () => {
    expect(
      localGraphConfiguration({
        OPEN_SWE_LOCAL_GRAPH_ORIGIN: "http://127.0.0.1:2024",
        OPEN_SWE_LOCAL_GRAPH_TOKEN: "test-token",
      })
    ).toEqual({ origin: "http://127.0.0.1:2024", token: "test-token" })

    expect(() =>
      localGraphConfiguration({
        OPEN_SWE_LOCAL_GRAPH_ORIGIN: "https://example.com",
        OPEN_SWE_LOCAL_GRAPH_TOKEN: "test-token",
      })
    ).toThrow("loopback HTTP origin")
    expect(() =>
      localGraphConfiguration({
        OPEN_SWE_LOCAL_GRAPH_ORIGIN: "http://127.0.0.1:2024",
      })
    ).toThrow("TOKEN")
  })

  it("removes only the public graph prefix and retains the query", () => {
    expect(
      localGraphTarget(
        new URL(
          "http://127.0.0.1:3000/local-graph/threads/id/runs?stream=true"
        ),
        "http://127.0.0.1:2024"
      )
    ).toBe("http://127.0.0.1:2024/threads/id/runs?stream=true")
  })
})

describe("local graph proxy origin gate", () => {
  const server = "http://127.0.0.1:3000"

  it("admits the app's own requests", () => {
    expect(isSameOriginRequest(server, "same-origin", server)).toBe(true)
    // A same-origin GET omits Origin, and Sec-Fetch-Site vouches for it.
    expect(isSameOriginRequest(undefined, "same-origin", server)).toBe(true)
    expect(isSameOriginRequest(undefined, "none", server)).toBe(true)
  })

  it("rejects a cross-site caller that would otherwise be proxied with the token", () => {
    expect(isSameOriginRequest("https://evil.example", undefined, server)).toBe(
      false
    )
    expect(
      isSameOriginRequest("https://evil.example", "cross-site", server)
    ).toBe(false)
    expect(isSameOriginRequest(undefined, "cross-site", server)).toBe(false)
    // Neither header: not a browser bound by the same-origin policy.
    expect(isSameOriginRequest(undefined, undefined, server)).toBe(false)
    // A different loopback port is a different origin.
    expect(
      isSameOriginRequest("http://127.0.0.1:4000", undefined, server)
    ).toBe(false)
  })
})
