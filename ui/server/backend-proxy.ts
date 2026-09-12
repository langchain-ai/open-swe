// Read per request, not at build time: which backend an instance fronts is a
// property of the deployment, so it lives in the pod's environment.
export function backendOrigin(): string {
  const configured = (process.env.DASHBOARD_API_URL ?? "").replace(/\/$/, "")
  if (!configured) {
    throw new Error(
      "DASHBOARD_API_URL is not set. It is the backend this dashboard fronts; " +
        "there is no default because a fallback would be production's backend."
    )
  }
  return configured
}

export const HOP_BY_HOP = new Set([
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
export const REFRAMED = new Set(["content-encoding", "content-length"])

// undici rejects a request that carries `transfer-encoding` outright; it frames
// the streamed body itself. `content-length` stays: with it undici sends a
// fixed-length body, without it a chunked one, and the backend must see the
// same framing the client used.
export function requestHeaders(incoming: Headers): Headers {
  const headers = new Headers()
  for (const [name, value] of incoming) {
    if (HOP_BY_HOP.has(name)) {
      continue
    }
    headers.set(name, value)
  }
  return headers
}

function acceptsGzip(value: string | null): boolean {
  const encodings = (value ?? "").split(",").map((part) => {
    const [name, ...parameters] = part.toLowerCase().split(";")
    const quality = parameters.find((param) => param.trim().startsWith("q="))
    return {
      name: name?.trim(),
      quality: quality === undefined ? 1 : Number(quality.trim().slice(2)),
    }
  })
  const accepted =
    encodings.find(({ name }) => name === "gzip") ??
    encodings.find(({ name }) => name === "*")
  return !!accepted && accepted.quality > 0 && accepted.quality <= 1
}

export default async function backendProxy(event: { req: Request }) {
  const url = new URL(event.req.url)
  const method = event.req.method

  const upstream = await fetch(
    `${backendOrigin()}${url.pathname}${url.search}`,
    {
      method,
      headers: requestHeaders(event.req.headers),
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

  let body = upstream.body
  if (
    body &&
    method !== "HEAD" &&
    upstream.status === 200 &&
    /^\/dashboard\/api\/threads\/[^/]+\/(state|history)\/?$/.test(
      url.pathname
    ) &&
    headers.get("content-type")?.split(";")[0]?.trim().toLowerCase() ===
      "application/json" &&
    !/(?:^|,)\s*no-transform\s*(?:,|$)/i.test(
      [
        event.req.headers.get("cache-control"),
        headers.get("cache-control"),
      ].join(",")
    )
  ) {
    const vary = (headers.get("vary") ?? "")
      .toLowerCase()
      .split(",")
      .map((name) => name.trim())
    if (!vary.includes("*") && !vary.includes("accept-encoding")) {
      headers.append("vary", "Accept-Encoding")
    }
    if (acceptsGzip(event.req.headers.get("accept-encoding"))) {
      body = body.pipeThrough(new CompressionStream("gzip"))
      headers.set("content-encoding", "gzip")
      const etag = headers.get("etag")
      if (etag && !etag.startsWith("W/")) headers.set("etag", `W/${etag}`)
    }
  }

  // A plain web Response, built here rather than by a proxy helper: the server
  // runtime bundles its own copy of h3, and a proxy result from a different one
  // is not a shape it recognises — it stringified it to `[object Object]` under
  // `text/plain`, which every dashboard query then failed to parse.
  return new Response(body, {
    status: upstream.status,
    statusText: upstream.statusText,
    headers,
  })
}
