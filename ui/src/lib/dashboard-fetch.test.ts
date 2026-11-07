import { afterEach, describe, expect, it, vi } from "vitest"

import { api } from "./api"

vi.mock("@tanstack/react-start/server", () => ({
  getRequest: () =>
    new Request("http://robin.example.com/admin/integrations", {
      headers: { "x-forwarded-proto": "https" },
    }),
  getRequestHeader: (name: string) =>
    name === "cookie" ? "osw_session=abc" : undefined,
}))

afterEach(() => vi.unstubAllGlobals())

describe("server-rendered dashboard requests", () => {
  it("name the domain the browser used and carry its session", async () => {
    const fetchMock = vi.fn().mockResolvedValue(Response.json({}))
    vi.stubGlobal("fetch", fetchMock)

    await api.me()

    const headers = (fetchMock.mock.calls[0] as [string, RequestInit])[1]
      .headers as Record<string, string>
    expect(headers["x-open-swe-public-origin"]).toBe(
      "https://robin.example.com"
    )
    expect(headers.cookie).toBe("osw_session=abc")
  })
})
