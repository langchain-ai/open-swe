/**
 * The document a plugin's frame loads: the plugin's own HTML behind a policy
 * that blocks its requests, so what it sends goes through the port the
 * dashboard hands it, as requests the backend checks.
 *
 * A frame can still navigate itself away, which no policy stops; the `csp`
 * attribute makes Chromium enforce this policy on whatever it navigates to,
 * and the dashboard hands its port only to the document it made.
 */
import { NONCE_META, type Theme } from "@openswe/plugin-sdk"

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

/**
 * No `allow-same-origin`: the frame gets an opaque origin, no cookies, and no
 * way into the page. `allow-forms` only lets forms fire `submit` for the
 * plugin's own handlers; `form-action 'none'` still blocks every submission.
 */
export const PLUGIN_SANDBOX = "allow-scripts allow-forms"
export const PLUGIN_ALLOW = "clipboard-write"

export function pluginDocument(
  html: string,
  theme: Theme,
  nonce: string
): string {
  // A meta policy governs only what follows it, so it comes before every byte
  // of the plugin's markup; a script ahead of it would run with the network open.
  // The plugin's own <html> and <head> tags merge into these.
  return (
    `<!doctype html><html data-theme="${theme}"><head>` +
    `<meta http-equiv="Content-Security-Policy" content="${PLUGIN_CSP}">` +
    `<meta name="${NONCE_META}" content="${nonce}">` +
    `<style>:root{color-scheme:${theme}}</style>` +
    html.replace(/^\s*<!doctype[^>]*>/i, "")
  )
}
