import { createHmac, timingSafeEqual } from "node:crypto"

/**
 * Restarts this container. `imagePullPolicy: Always` makes the runtime re-resolve
 * the image tag when the kubelet launches the replacement, so exiting is what
 * picks up a newly published image — no cluster credentials anywhere, and a
 * caller holding this secret can do nothing but restart this one service.
 */
const RESTART_ACTION = "restart"

// A minted token is used within seconds of a publish; a long window would leave a
// replayable restart lying around in CI logs.
const MAX_LIFETIME_SECONDS = 300

function decodeSegment(segment: string): unknown {
  return JSON.parse(Buffer.from(segment, "base64url").toString("utf8"))
}

function signatureMatches(signed: string, signature: string, key: string) {
  const expected = createHmac("sha256", key).update(signed).digest()
  const received = Buffer.from(signature, "base64url")
  return (
    expected.length === received.length && timingSafeEqual(expected, received)
  )
}

function tokenIsValid(token: string, key: string): boolean {
  const [encodedHeader, encodedClaims, signature, ...rest] = token.split(".")
  if (!encodedHeader || !encodedClaims || !signature || rest.length)
    return false

  if (!signatureMatches(`${encodedHeader}.${encodedClaims}`, signature, key)) {
    return false
  }

  try {
    // Pinned rather than read from the token: honouring the header's `alg` is what
    // lets a caller downgrade to `none` and sign nothing.
    const header = decodeSegment(encodedHeader) as { alg?: unknown }
    if (header.alg !== "HS256") return false

    const claims = decodeSegment(encodedClaims) as {
      act?: unknown
      exp?: unknown
    }
    if (claims.act !== RESTART_ACTION) return false
    if (typeof claims.exp !== "number") return false

    const now = Math.floor(Date.now() / 1000)
    return claims.exp > now && claims.exp - now <= MAX_LIFETIME_SECONDS
  } catch {
    return false
  }
}

export default async function operationsRestart(event: {
  req: Request
}): Promise<Response> {
  if (event.req.method !== "POST") {
    return new Response("method not allowed\n", { status: 405 })
  }

  const key = process.env.OPERATIONS_API_JWT_SECRET ?? ""
  if (!key) {
    // Absent secret closes the endpoint rather than opening it.
    return new Response("not found\n", { status: 404 })
  }

  const header = event.req.headers.get("authorization") ?? ""
  const token = header.startsWith("Bearer ") ? header.slice(7) : ""
  if (!token || !tokenIsValid(token, key)) {
    return new Response("unauthorized\n", { status: 401 })
  }

  // Exit after the response is on the wire, so the caller sees 202 rather than a
  // dropped connection it cannot distinguish from a network fault.
  setTimeout(() => process.exit(0), 250)

  return new Response("restarting\n", { status: 202 })
}
