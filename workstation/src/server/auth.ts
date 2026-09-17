import { createHash, createHmac, timingSafeEqual } from "node:crypto"

/**
 * Request authentication for the workstation server.
 *
 * The LangSmith tunnel that fronts this server is reachable by anyone holding
 * an organization-level API key, so the tunnel is not access control: this
 * module is. Every route is gated on a signature over the method, the request
 * target, a timestamp and a hash of the exact body bytes, and every failure
 * path returns a refusal rather than an exception.
 */

export interface AuthConfig {
  readonly secret: string
  readonly maxSkewSeconds?: number
  readonly replay?: ReplayGuard
}

export interface ReplayGuard {
  /** False when this signature was already accepted inside the skew window. */
  accept(signature: string, nowMs?: number): boolean
}

/**
 * Single-use signatures, so a captured request cannot be replayed for the rest
 * of its skew window. It matters most for `execute`, where a replay re-runs an
 * arbitrary command.
 *
 * Only signatures that already passed verification are recorded, so reaching
 * this map costs an attacker a forged HMAC and the size stays bounded by the
 * legitimate request rate.
 */
export function createReplayGuard(ttlSeconds: number): ReplayGuard {
  const seen = new Map<string, number>()
  return {
    accept(signature: string, nowMs: number = Date.now()): boolean {
      for (const [key, expiry] of seen) {
        if (expiry <= nowMs) seen.delete(key)
      }
      if (seen.has(signature)) return false
      seen.set(signature, nowMs + ttlSeconds * 1000)
      return true
    },
  }
}

export interface SignatureInput {
  readonly method: string
  readonly path: string
  readonly body: Uint8Array
  readonly timestamp: string
}

export type VerifyResult =
  | { readonly ok: true }
  | { readonly ok: false; readonly reason: string }

const SIGNATURE_VERSION = "v1"
const DEFAULT_MAX_SKEW_SECONDS = 300
const HEX_SIGNATURE = /^[0-9a-fA-F]{64}$/
const UNIX_SECONDS = /^-?[0-9]{1,15}$/

function canonicalString(input: SignatureInput): string {
  const bodyHash = createHash("sha256").update(input.body).digest("hex")
  return [
    SIGNATURE_VERSION,
    input.method.toUpperCase(),
    input.path,
    input.timestamp,
    bodyHash,
  ].join("\n")
}

/**
 * Compare through fixed-width digests so unequal input lengths neither throw
 * out of `timingSafeEqual` nor return early on a length mismatch.
 */
function digestEqual(left: string, right: string): boolean {
  const leftDigest = createHash("sha256").update(left, "utf8").digest()
  const rightDigest = createHash("sha256").update(right, "utf8").digest()
  return timingSafeEqual(leftDigest, rightDigest)
}

export function signRequest(secret: string, input: SignatureInput): string {
  return createHmac("sha256", secret)
    .update(canonicalString(input), "utf8")
    .digest("hex")
}

export function verifyRequest(
  config: AuthConfig,
  input: SignatureInput & { readonly signature: string }
): VerifyResult {
  if (config.secret.length === 0) {
    return { ok: false, reason: "server_secret_unset" }
  }
  if (input.method.length === 0 || input.path.length === 0) {
    return { ok: false, reason: "malformed_request" }
  }
  if (!HEX_SIGNATURE.test(input.signature)) {
    return { ok: false, reason: "malformed_signature" }
  }
  if (!UNIX_SECONDS.test(input.timestamp)) {
    return { ok: false, reason: "malformed_timestamp" }
  }

  const maxSkewSeconds = config.maxSkewSeconds ?? DEFAULT_MAX_SKEW_SECONDS
  const timestamp = Number(input.timestamp)
  if (!Number.isFinite(timestamp)) {
    return { ok: false, reason: "malformed_timestamp" }
  }
  const skew = Math.abs(Date.now() / 1000 - timestamp)
  if (skew > maxSkewSeconds) {
    return { ok: false, reason: "timestamp_out_of_range" }
  }

  const signature = input.signature.toLowerCase()
  const expected = signRequest(config.secret, input)
  if (!digestEqual(expected, signature)) {
    return { ok: false, reason: "signature_mismatch" }
  }
  // After the HMAC, so an unauthenticated caller cannot seed the guard.
  if (config.replay !== undefined && !config.replay.accept(signature)) {
    return { ok: false, reason: "signature_replayed" }
  }
  return { ok: true }
}
