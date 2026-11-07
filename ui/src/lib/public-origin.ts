// Tells the backend which domain the browser used, so one that serves several
// (DASHBOARD_ALIAS_URLS) answers sign-in and MCP on the domain it was reached
// through. Always overwritten, never passed through from the client.
export const PUBLIC_ORIGIN_HEADER = "x-open-swe-public-origin"

// TLS ends at the ingress, so the scheme comes from its X-Forwarded-Proto.
export function publicOrigin(req: Request): string {
  const url = new URL(req.url)
  const forwarded = req.headers.get("x-forwarded-proto")?.split(",")[0]?.trim()
  const scheme =
    forwarded === "https" || forwarded === "http"
      ? forwarded
      : url.protocol.replace(/:$/, "")
  return `${scheme}://${url.host}`
}
