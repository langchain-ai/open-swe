export const ARTIFACT_CSP = [
  "default-src 'none'",
  "script-src 'unsafe-inline'",
  "style-src 'unsafe-inline' https://fonts.googleapis.com",
  "font-src https://fonts.gstatic.com data:",
  "img-src data: blob:",
  "media-src data: blob:",
  "base-uri 'none'",
  "form-action 'none'",
  "frame-src 'none'",
  "connect-src 'none'",
].join("; ")

export const ARTIFACT_SANDBOX = "allow-scripts allow-downloads"
export const ARTIFACT_ALLOW = "clipboard-write"

export function withArtifactShell(
  html: string,
  theme: "light" | "dark"
): string {
  // Seeds the UA-rendered parts (scrollbars, form controls) with the viewer's
  // theme; an artifact's own `color-scheme` declaration still wins.
  const head =
    `<meta http-equiv="Content-Security-Policy" content="${ARTIFACT_CSP}">` +
    `<style>:root{color-scheme:${theme}}</style>`
  const themed = html.replace(
    /<html(?=\s|>)/i,
    `<html data-theme="${theme}" data-viewer-theme="${theme}"`
  )
  if (/<head(?=\s|>)/i.test(themed)) {
    return themed.replace(/<head([^>]*)>/i, `<head$1>${head}`)
  }
  return `<!doctype html><html data-theme="${theme}" data-viewer-theme="${theme}"><head>${head}</head><body>${themed}</body></html>`
}
