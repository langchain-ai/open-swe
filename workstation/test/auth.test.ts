import assert from "node:assert/strict"
import test from "node:test"

import {
  type AuthConfig,
  createReplayGuard,
  signRequest,
  type SignatureInput,
  verifyRequest,
} from "../src/server/auth.ts"

const SECRET = "workstation-test-secret"

function seconds(offset = 0): string {
  return String(Math.floor(Date.now() / 1000) + offset)
}

function input(overrides: Partial<SignatureInput> = {}): SignatureInput {
  return {
    method: "POST",
    path: "/v1/fs/read",
    body: new TextEncoder().encode('{"root":"/tmp/project"}'),
    timestamp: seconds(),
    ...overrides,
  }
}

function verify(
  signed: SignatureInput,
  presented: SignatureInput,
  config: Partial<AuthConfig> = {}
) {
  return verifyRequest(
    { secret: SECRET, ...config },
    { ...presented, signature: signRequest(SECRET, signed) }
  )
}

test("a signature over the presented request verifies", () => {
  const request = input()
  assert.deepEqual(verify(request, request), { ok: true })
})

test("signRequest is deterministic and hex-encoded", () => {
  const request = input()
  const signature = signRequest(SECRET, request)
  assert.match(signature, /^[0-9a-f]{64}$/)
  assert.equal(signature, signRequest(SECRET, request))
})

test("a body tampered after signing is rejected", () => {
  const signed = input()
  const presented = input({
    timestamp: signed.timestamp,
    body: new TextEncoder().encode('{"root":"/tmp/other"}'),
  })
  assert.deepEqual(verify(signed, presented), {
    ok: false,
    reason: "signature_mismatch",
  })
})

test("a signature replayed on another path is rejected", () => {
  const signed = input({ path: "/v1/fs/read" })
  const presented = input({
    timestamp: signed.timestamp,
    path: "/v1/execute",
  })
  assert.deepEqual(verify(signed, presented), {
    ok: false,
    reason: "signature_mismatch",
  })
})

test("a signature replayed with another method is rejected", () => {
  const signed = input({ method: "POST" })
  const presented = input({ timestamp: signed.timestamp, method: "DELETE" })
  assert.deepEqual(verify(signed, presented), {
    ok: false,
    reason: "signature_mismatch",
  })
})

test("a signature from another secret is rejected", () => {
  const request = input()
  const result = verifyRequest(
    { secret: SECRET },
    { ...request, signature: signRequest("other-secret", request) }
  )
  assert.deepEqual(result, { ok: false, reason: "signature_mismatch" })
})

test("a timestamp beyond the default skew is rejected in both directions", () => {
  for (const offset of [-301, 301]) {
    const request = input({ timestamp: seconds(offset) })
    assert.deepEqual(verify(request, request), {
      ok: false,
      reason: "timestamp_out_of_range",
    })
  }
})

test("a timestamp inside the skew window is accepted", () => {
  for (const offset of [-299, 0, 299]) {
    const request = input({ timestamp: seconds(offset) })
    assert.deepEqual(verify(request, request), { ok: true })
  }
})

test("maxSkewSeconds narrows the replay window", () => {
  const request = input({ timestamp: seconds(-20) })
  assert.deepEqual(verify(request, request, { maxSkewSeconds: 10 }), {
    ok: false,
    reason: "timestamp_out_of_range",
  })
  assert.deepEqual(verify(request, request, { maxSkewSeconds: 30 }), {
    ok: true,
  })
})

test("a malformed timestamp is rejected without throwing", () => {
  for (const timestamp of [
    "",
    "abc",
    "1.5",
    "1e9",
    " 100",
    "1700000000000000000000",
  ]) {
    const request = input({ timestamp })
    assert.deepEqual(verify(request, request), {
      ok: false,
      reason: "malformed_timestamp",
    })
  }
})

test("a missing or malformed signature is rejected without throwing", () => {
  const request = input()
  for (const signature of [
    "",
    "deadbeef",
    "z".repeat(64),
    "a".repeat(63),
    "a".repeat(65),
  ]) {
    assert.deepEqual(
      verifyRequest({ secret: SECRET }, { ...request, signature }),
      {
        ok: false,
        reason: "malformed_signature",
      }
    )
  }
})

test("a wrong signature of the correct length is rejected", () => {
  const request = input()
  assert.deepEqual(
    verifyRequest(
      { secret: SECRET },
      { ...request, signature: "0".repeat(64) }
    ),
    { ok: false, reason: "signature_mismatch" }
  )
})

test("an upper-case hex signature verifies", () => {
  const request = input()
  const signature = signRequest(SECRET, request).toUpperCase()
  assert.deepEqual(
    verifyRequest({ secret: SECRET }, { ...request, signature }),
    {
      ok: true,
    }
  )
})

test("an unset server secret fails closed", () => {
  const request = input()
  assert.deepEqual(
    verifyRequest({ secret: "" }, { ...request, signature: "a".repeat(64) }),
    { ok: false, reason: "server_secret_unset" }
  )
})

test("an empty method or path is rejected", () => {
  for (const overrides of [{ method: "" }, { path: "" }]) {
    const request = input(overrides)
    assert.deepEqual(verify(request, request), {
      ok: false,
      reason: "malformed_request",
    })
  }
})

test("the secret does not appear in a signature or a rejection reason", () => {
  const request = input()
  const signature = signRequest(SECRET, request)
  assert.ok(!signature.includes(SECRET))
  const rejected = verifyRequest(
    { secret: SECRET },
    { ...request, signature: "0".repeat(64) }
  )
  assert.ok(!JSON.stringify(rejected).includes(SECRET))
})

test("the replay guard admits a signature once and forgets it after its ttl", () => {
  const guard = createReplayGuard(300)
  assert.equal(guard.accept("a".repeat(64), 1_000_000), true)
  assert.equal(guard.accept("a".repeat(64), 1_000_000), false)
  assert.equal(guard.accept("b".repeat(64), 1_000_000), true)
  assert.equal(guard.accept("a".repeat(64), 1_000_000 + 300_001), true)
})
