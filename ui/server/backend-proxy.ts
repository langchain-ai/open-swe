import {
  Agent as HttpAgent,
  type IncomingMessage,
  request as httpRequest,
} from "node:http"
import { Agent as HttpsAgent, request as httpsRequest } from "node:https"
import { Readable } from "node:stream"
import { pipeline } from "node:stream/promises"
// The DOM lib and node type the same runtime streams differently.
import type { ReadableStream as NodeReadableStream } from "node:stream/web"
import { PUBLIC_ORIGIN_HEADER, publicOrigin } from "../src/lib/public-origin"

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

// Statuses a web Response refuses a body for.
const BODILESS = new Set([204, 205, 304])

// Idle connections close after 4 s, a second under uvicorn's default 5 s
// keep-alive: a request sent as the backend closes one fails with ECONNRESET.
const HTTP = {
  send: httpRequest,
  agent: new HttpAgent({ keepAlive: true, timeout: 4_000 }),
}
const HTTPS = {
  send: httpsRequest,
  agent: new HttpsAgent({ keepAlive: true, timeout: 4_000 }),
}

// Framing follows the body the server runtime hands over, a stream for every
// method but GET and HEAD, even an empty one: the client's content-length, or
// chunked without one, which node only does unasked for POST, PUT and PATCH.
// With no body the length goes too. Host is this dashboard's, and node would
// send it on as the backend's TLS name too.
export function requestHeaders(req: Request): Record<string, string> {
  const headers: Record<string, string> = {}
  for (const [name, value] of req.headers) {
    if (HOP_BY_HOP.has(name) || name === "host") {
      continue
    }
    headers[name] = value
  }
  if (!req.body) {
    delete headers["content-length"]
  } else if (headers["content-length"] === undefined) {
    headers["transfer-encoding"] = "chunked"
  }
  return headers
}

export default async function backendProxy(event: { req: Request }) {
  const url = new URL(event.req.url)
  const target = new URL(`${backendOrigin()}${url.pathname}${url.search}`)
  const headers = requestHeaders(event.req)
  headers[PUBLIC_ORIGIN_HEADER] = publicOrigin(event.req)

  // node:http rather than fetch, which decodes a compressed body: the backend's
  // gzip reaches the browser as sent.
  const upstream = await new Promise<IncomingMessage>((resolve, reject) => {
    const { send, agent } = target.protocol === "https:" ? HTTPS : HTTP
    const outgoing = send(
      target,
      { method: event.req.method, headers, agent, signal: event.req.signal },
      resolve
    )
    // Stays attached after the response: a connection reset mid-body errors
    // here as well as on `upstream`, and an unheard error is an uncaught one.
    outgoing.on("error", reject)
    if (event.req.body) {
      pipeline(event.req.body as NodeReadableStream, outgoing).catch(reject)
    } else {
      outgoing.end()
    }
  }).catch((error: unknown) => {
    // The client left and its signal cut the backend request: no one to answer.
    if (event.req.signal.aborted) {
      return null
    }
    throw error
  })
  if (!upstream) {
    return new Response(null, { status: 499 })
  }

  const responseHeaders = new Headers()
  for (const [name, values = []] of Object.entries(upstream.headersDistinct)) {
    if (HOP_BY_HOP.has(name)) {
      continue
    }
    // Every cookie keeps its own header line.
    for (const value of values) {
      responseHeaders.append(name, value)
    }
  }

  const status = upstream.statusCode!
  const bodiless = BODILESS.has(status)
  if (bodiless) {
    // Drained, or its socket never goes back to the pool.
    upstream.resume()
  }
  // A plain web Response, built here rather than by a proxy helper: the server
  // runtime bundles its own copy of h3, and a proxy result from a different one
  // is not a shape it recognises — it stringified it to `[object Object]` under
  // `text/plain`, which every dashboard query then failed to parse.
  return new Response(
    bodiless ? null : (Readable.toWeb(upstream) as ReadableStream),
    {
      status,
      statusText: upstream.statusMessage,
      headers: responseHeaders,
    }
  )
}
