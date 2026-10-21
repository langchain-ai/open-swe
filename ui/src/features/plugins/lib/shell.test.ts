import { describe, expect, it } from "vitest"

import { PLUGIN_CSP, pluginDocument } from "@/features/plugins/lib/shell"

describe("pluginDocument", () => {
  it("puts the no-network policy ahead of everything the plugin wrote", () => {
    // A script before <head> would run before a policy injected into <head>.
    const html = pluginDocument(
      "<!doctype html><script>fetch('https://evil.test')</script><html><head></head><body></body></html>",
      "dark",
      "nonce-1"
    )
    const policy = html.indexOf(PLUGIN_CSP)
    expect(policy).toBeGreaterThan(0)
    expect(policy).toBeLessThan(html.indexOf("<script>"))
    expect(html.indexOf('content="nonce-1"')).toBeLessThan(
      html.indexOf("<script>")
    )
    expect(PLUGIN_CSP).toContain("connect-src 'none'")
  })
})
