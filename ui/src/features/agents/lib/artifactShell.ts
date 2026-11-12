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

/** Whether `html` opens as a full document once its leading comments are skipped. */
function startsAsDocument(html: string): boolean {
  let rest = html.trimStart()
  // A loop, not a regex: repeated comments would make a regex backtrack.
  while (rest.startsWith("<!--")) {
    const end = rest.indexOf("-->", 4)
    if (end < 0) return false
    rest = rest.slice(end + 3).trimStart()
  }
  return /^<(?:!doctype|html|head|body)[\s>]/i.test(rest)
}

export function withArtifactShell(
  html: string,
  theme: "light" | "dark"
): string {
  // Seeds the UA-rendered parts (scrollbars, form controls) with the viewer's
  // theme; an artifact's own `color-scheme` declaration still wins.
  // The policy precedes every byte of the artifact; never write it after `html`.
  // A fragment gets its body opened for it, so its first script finds one.
  const structured = startsAsDocument(html)
  return (
    `<!doctype html><html data-theme="${theme}" data-viewer-theme="${theme}"><head>` +
    `<meta http-equiv="Content-Security-Policy" content="${ARTIFACT_CSP}">` +
    `<style>:root{color-scheme:${theme}}</style>` +
    (structured ? "" : "</head><body>") +
    html
  )
}
