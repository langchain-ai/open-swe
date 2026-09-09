import { createWebSocketProxy } from "crossws"
import {
  HOP_BY_HOP,
  REFRAMED,
  backendOrigin,
  requestHeaders,
} from "./backend-proxy"

// The header LangSmith's sandbox gateway authenticates a service request with.
// Keeping the token here rather than in the URL is the point of this proxy: the
// browser never holds a sandbox credential, and the link stays short.
const SERVICE_TOKEN_HEADER = "x-langsmith-sandbox-service-token"

// Cookies this dashboard sets for itself. A sandbox serves agent-authored code,
// so it must not see the session that authorizes this proxy in the first place.
const DASHBOARD_COOKIE_PREFIX = "osw_"

const TOKEN_REFRESH_MARGIN_MS = 30_000
const TOKEN_CACHE_LIMIT = 128

type Target = { threadId: string; port: number; prefix: string; path: string }
type Service = { serviceUrl: string; token: string; expiresAtMs: number }

const services = new Map<string, Service>()

class ProxyError extends Error {
  constructor(
    readonly status: number,
    message: string
  ) {
    super(message)
  }
}

// `/sandbox/<thread>/<port>/<path>`. The upstream sees `<path>` alone: it serves
// its own root and knows nothing of the prefix this proxy mounts it under.
function parseTarget(pathname: string): Target | null {
  const match = /^\/sandbox\/([^/]+)\/(\d{1,5})(\/.*)?$/.exec(pathname)
  if (!match) {
    return null
  }
  const rawThreadId = match[1] ?? ""
  const rawPort = match[2] ?? ""
  const path = match[3] ?? ""
  const threadId = decodeURIComponent(rawThreadId)
  const port = Number(rawPort)
  if (!/^[\w.-]{1,200}$/.test(threadId) || port < 1 || port > 65535) {
    return null
  }
  return {
    threadId,
    port,
    prefix: `/sandbox/${rawThreadId}/${rawPort}`,
    path,
  }
}

// Only what authorizes the caller, so cookies the service sets for itself do not
// churn the cache below.
function credential(headers: Headers): string {
  const session = (headers.get("cookie") ?? "")
    .split(";")
    .map((cookie) => cookie.trim())
    .filter((cookie) => cookie.startsWith(DASHBOARD_COOKIE_PREFIX))
    .join("; ")
  return `${session} ${headers.get("authorization") ?? ""}`
}

// Keyed by the caller's own credential, so a cached token is only ever reused by
// the identity the backend authorized it for.
async function resolveService(
  target: Target,
  headers: Headers
): Promise<Service> {
  const key = `${target.threadId} ${target.port} ${credential(headers)}`
  const cached = services.get(key)
  if (cached && cached.expiresAtMs - TOKEN_REFRESH_MARGIN_MS > Date.now()) {
    return cached
  }

  const mint = new Headers()
  for (const name of ["cookie", "authorization"]) {
    const value = headers.get(name)
    if (value) {
      mint.set(name, value)
    }
  }
  const url =
    `${backendOrigin()}/dashboard/api/threads/${encodeURIComponent(target.threadId)}` +
    `/service-url?port=${target.port}`
  const response = await fetch(url, { headers: mint })
  if (!response.ok) {
    // The backend owns who may reach a thread's sandbox, so pass its own refusal
    // through: an expired session then reads as one instead of a proxy failure.
    const status =
      response.status >= 400 && response.status < 500 ? response.status : 502
    throw new ProxyError(status, "This sandbox service is not available.")
  }
  const body = (await response.json()) as {
    service_url?: unknown
    token?: unknown
    expires_at?: unknown
  }
  if (typeof body.service_url !== "string" || typeof body.token !== "string") {
    throw new ProxyError(502, "This sandbox service is not available.")
  }
  const expiresAtMs =
    typeof body.expires_at === "string"
      ? Date.parse(body.expires_at)
      : Number.NaN
  const service: Service = {
    serviceUrl: body.service_url.replace(/\/$/, ""),
    token: body.token,
    expiresAtMs: Number.isNaN(expiresAtMs) ? Date.now() : expiresAtMs,
  }
  if (services.size >= TOKEN_CACHE_LIMIT) {
    services.delete(services.keys().next().value as string)
  }
  services.set(key, service)
  return service
}

function upstreamHeaders(incoming: Headers, service: Service): Headers {
  const headers = requestHeaders(incoming)
  headers.delete("host")
  headers.delete("authorization")
  const cookies = (headers.get("cookie") ?? "")
    .split(";")
    .map((cookie) => cookie.trim())
    .filter((cookie) => cookie && !cookie.startsWith(DASHBOARD_COOKIE_PREFIX))
  if (cookies.length) {
    headers.set("cookie", cookies.join("; "))
  } else {
    headers.delete("cookie")
  }
  headers.set(SERVICE_TOKEN_HEADER, service.token)
  return headers
}

function rewriteLocation(
  value: string,
  service: Service,
  target: Target
): string {
  if (value.startsWith("//")) {
    return value
  }
  if (value.startsWith("/")) {
    return `${target.prefix}${value}`
  }
  if (value.startsWith(`${service.serviceUrl}/`)) {
    return `${target.prefix}${value.slice(service.serviceUrl.length)}`
  }
  return value
}

// Scope the service's cookies to its own prefix: unscoped they would ride along
// on every dashboard request, and a `Domain` from the sandbox means nothing here.
function rewriteSetCookie(value: string, target: Target): string {
  const attributes = value.split(";").filter((attribute) => {
    const name = attribute.trim().toLowerCase()
    return !name.startsWith("domain=") && !name.startsWith("path=")
  })
  attributes.push(` Path=${target.prefix}/`)
  return attributes.join(";")
}

function isWebSocketUpgrade(req: Request): boolean {
  return (req.headers.get("upgrade") ?? "").toLowerCase() === "websocket"
}

// h3 upgrades a response carrying `crossws` hooks, so the resolved token reaches
// the upstream handshake the same way it reaches a plain request.
function webSocketResponse(
  service: Service,
  target: Target,
  search: string
): Response {
  const upstream = new URL(
    `${target.path}${search}`,
    service.serviceUrl.replace(/^http/, "ws")
  )
  return Object.assign(
    new Response("WebSocket upgrade is required.", { status: 426 }),
    {
      crossws: createWebSocketProxy({
        target: upstream.href,
        headers: { [SERVICE_TOKEN_HEADER]: service.token },
      }),
    }
  )
}

export default async function sandboxProxy(event: { req: Request }) {
  const url = new URL(event.req.url)
  const target = parseTarget(url.pathname)
  if (!target) {
    return new Response("Not found.", { status: 404 })
  }
  // Without the trailing slash a browser resolves the service's relative URLs
  // against the port segment's parent, one level above the service's own root.
  if (!target.path) {
    return new Response(null, {
      status: 308,
      headers: { location: `${target.prefix}/${url.search}` },
    })
  }

  let service: Service
  try {
    service = await resolveService(target, event.req.headers)
  } catch (error) {
    return new Response(
      error instanceof ProxyError
        ? error.message
        : "This sandbox service is not available.",
      { status: error instanceof ProxyError ? error.status : 502 }
    )
  }

  if (isWebSocketUpgrade(event.req)) {
    return webSocketResponse(service, target, url.search)
  }

  const method = event.req.method
  const upstream = await fetch(
    `${service.serviceUrl}${target.path}${url.search}`,
    {
      method,
      headers: upstreamHeaders(event.req.headers, service),
      body: method === "GET" || method === "HEAD" ? undefined : event.req.body,
      redirect: "manual",
      ...({ duplex: "half" } as RequestInit),
    }
  )

  const headers = new Headers()
  for (const [name, value] of upstream.headers) {
    if (HOP_BY_HOP.has(name) || REFRAMED.has(name) || name === "set-cookie") {
      continue
    }
    headers.set(
      name,
      name === "location" ? rewriteLocation(value, service, target) : value
    )
  }
  // Every cookie needs its own header line; iterating above would join them.
  for (const cookie of upstream.headers.getSetCookie()) {
    headers.append("set-cookie", rewriteSetCookie(cookie, target))
  }

  return new Response(upstream.body, {
    status: upstream.status,
    statusText: upstream.statusText,
    headers,
  })
}
