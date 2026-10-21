/**
 * The document a plugin's frame loads: the plugin's own HTML behind a policy
 * that gives it no network. Its only way out is the port the dashboard hands
 * it, so everything it sends anywhere goes through a request the backend checks.
 */
import type { Theme } from "@openswe/plugin-sdk"

export const PLUGIN_CSP = [
  "default-src 'none'",
  "script-src 'unsafe-inline'",
  "style-src 'unsafe-inline'",
  "font-src data:",
  "img-src data: blob:",
  "media-src data: blob:",
  "base-uri 'none'",
  "form-action 'none'",
  "frame-src 'none'",
  "worker-src 'none'",
  "connect-src 'none'",
].join("; ")

/** No `allow-same-origin`: the frame gets an opaque origin, no cookies, and no way into the page. */
export const PLUGIN_SANDBOX = "allow-scripts"
export const PLUGIN_ALLOW = "clipboard-write"

export function pluginDocument(html: string, theme: Theme): string {
  // A meta policy governs only what follows it, so it comes before every byte
  // of the plugin's markup; a script ahead of it would run with the network open.
  // The plugin's own <html> and <head> tags merge into these.
  return (
    `<!doctype html><html data-theme="${theme}"><head>` +
    `<meta http-equiv="Content-Security-Policy" content="${PLUGIN_CSP}">` +
    `<style>:root{color-scheme:${theme}}</style>` +
    html.replace(/^\s*<!doctype[^>]*>/i, "")
  )
}
