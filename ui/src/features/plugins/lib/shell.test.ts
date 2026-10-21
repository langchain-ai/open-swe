import { describe, expect, it } from "vitest"

import {
  PLUGIN_CSP,
  pluginDocument,
  pluginShell,
} from "@/features/plugins/lib/shell"

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

describe("pluginShell", () => {
  it("frames the plugin under frame-src 'none', so it cannot navigate away", () => {
    const shell = pluginShell('<p title="x">&</p>', "light", "nonce-1")
    const policy = shell.indexOf("frame-src 'none'")
    expect(policy).toBeGreaterThan(0)
    expect(policy).toBeLessThan(shell.indexOf("<iframe"))
    // The plugin's markup sits escaped inside srcdoc, never as shell markup.
    expect(shell).toContain("<p title=&quot;x&quot;>&amp;</p>")
    expect(shell).not.toContain('<p title="x">')
  })
})
