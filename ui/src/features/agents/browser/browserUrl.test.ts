import { describe, expect, it } from "vitest"

import {
  BrowserUrlError,
  browserUrlHost,
  isLoopbackHost,
  isWebUrl,
  normalizeBrowserUrl,
} from "@/features/agents/browser/browserUrl"

describe("normalizeBrowserUrl", () => {
  it("defaults loopback hosts to http and everything else to https", () => {
    expect(normalizeBrowserUrl("localhost:5173")).toBe("http://localhost:5173/")
    expect(normalizeBrowserUrl("127.0.0.1")).toBe("http://127.0.0.1/")
    expect(normalizeBrowserUrl("[::1]:3000/app")).toBe("http://[::1]:3000/app")
    expect(normalizeBrowserUrl("example.com/docs")).toBe(
      "https://example.com/docs"
    )
    expect(normalizeBrowserUrl("example.com:8080")).toBe(
      "https://example.com:8080/"
    )
  })

  it("keeps qualified URLs and trims whitespace", () => {
    expect(normalizeBrowserUrl("  http://localhost:8080/?a=1 ")).toBe(
      "http://localhost:8080/?a=1"
    )
  })

  it("rejects empty input and non-web schemes", () => {
    expect(() => normalizeBrowserUrl("   ")).toThrow(BrowserUrlError)
    expect(() => normalizeBrowserUrl("javascript:alert(1)")).toThrowError(
      /Only http and https/
    )
    expect(() => normalizeBrowserUrl("file:///etc/passwd")).toThrowError(
      /Only http and https/
    )
    expect(() => normalizeBrowserUrl("data:text/html,hi")).toThrowError(
      /Only http and https/
    )
    // The host portion carries a literal space, which the URL parser rejects.
    expect(() => normalizeBrowserUrl("http://exa mple.com")).toThrowError(
      /valid URL/
    )
  })
})

describe("url predicates", () => {
  it("classifies loopback hosts and web urls", () => {
    expect(isLoopbackHost("LOCALHOST")).toBe(true)
    expect(isLoopbackHost("example.com")).toBe(false)
    expect(isWebUrl("https://example.com")).toBe(true)
    expect(isWebUrl("mailto:someone@example.com")).toBe(false)
    expect(isWebUrl("not a url")).toBe(false)
  })

  it("labels a tab by host and falls back to the raw string", () => {
    expect(browserUrlHost("http://localhost:5173/some/page")).toBe(
      "localhost:5173"
    )
    expect(browserUrlHost("garbage")).toBe("garbage")
  })
})
