// Read per request, not at build time: which backend an instance fronts is a
// property of the deployment, so it lives in the pod's environment.
function backendOrigin(): string {
  const configured = (process.env.DASHBOARD_API_URL ?? "").replace(/\/$/, "")
  if (!configured) {
    throw new Error(
      "DASHBOARD_API_URL is not set. It is the backend this dashboard fronts; " +
        "there is no default because a fallback would be production's backend."
    )
  }
  return configured
}

const HOP_BY_HOP = new Set([
  "connection",
  "keep-alive",
  "proxy-authenticate",
  "proxy-authorization",
  "te",
  "trailer",
  "transfer-encoding",
  "upgrade",
])

// `fetch` hands back a decoded body while leaving the upstream's encoding and
// length headers in place, so forwarding those would describe bytes the client
// never receives.
const REFRAMED = new Set(["content-encoding", "content-length"])

export default async function backendProxy(event: { req: Request }) {
  const url = new URL(event.req.url)
  const method = event.req.method

  const upstream = await fetch(
    `${backendOrigin()}${url.pathname}${url.search}`,
    {
      method,
      headers: event.req.headers,
      body: method === "GET" || method === "HEAD" ? undefined : event.req.body,
      // `manual` keeps the OAuth 3xx hops intact — following them here would
      // leave the browser's address bar where it started.
      redirect: "manual",
      ...({ duplex: "half" } as RequestInit),
    }
  )

  const headers = new Headers()
  for (const [name, value] of upstream.headers) {
    if (HOP_BY_HOP.has(name) || REFRAMED.has(name) || name === "set-cookie") {
      continue
    }
    headers.set(name, value)
  }
  // Every cookie needs its own header line; iterating above would join them.
  for (const cookie of upstream.headers.getSetCookie()) {
    headers.append("set-cookie", cookie)
  }

  // A plain web Response, built here rather than by a proxy helper: the server
  // runtime bundles its own copy of h3, and a proxy result from a different one
  // is not a shape it recognises — it stringified it to `[object Object]` under
  // `text/plain`, which every dashboard query then failed to parse.
  return new Response(upstream.body, {
    status: upstream.status,
    statusText: upstream.statusText,
    headers,
  })
}
