import { describe, expect, it } from "vitest"

import { localGraphConfiguration, localGraphTarget } from "./local-graph-proxy"

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
        new URL("http://127.0.0.1:3000/local-graph/threads/id/runs?stream=true"),
        "http://127.0.0.1:2024"
      )
    ).toBe("http://127.0.0.1:2024/threads/id/runs?stream=true")
  })
})
