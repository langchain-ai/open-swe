/**
 * Stub backend for exercising desktop browser login without GitHub or a real
 * server. Skips straight from /auth/login to the loopback redirect, then logs
 * every proxied request so you can see the session cookie arrive.
 *
 *   node desktop/test/manual/stub-backend.cjs 4999
 *   OPEN_SWE_BACKEND_URL=http://127.0.0.1:4999 pnpm --dir desktop run dev
 */
const http = require("node:http")
const { createHash, randomBytes } = require("node:crypto")

const port = Number(process.argv[2] || 4999)
const handoffs = new Map()

const MODEL = {
  id: "anthropic:claude-opus-5",
  label: "Opus 5",
  efforts: ["low", "medium", "high", "xhigh", "max"],
  default_effort: "high",
  supports_images: true,
}

// Enough shape for the dashboard to render once signed in; everything else
// falls through to an empty list.
const SIGNED_IN_ROUTES = {
  "/dashboard/api/me": {
    login: "stub-user",
    email: "stub@example.com",
    avatar_url: null,
    is_admin: false,
    slack_oauth_enabled: false,
  },
  "/dashboard/api/threads/sidebar": {
    active: { items: [], limit: 50, hasMore: false },
    resolved: { items: [], limit: 20, hasMore: false },
  },
  "/dashboard/api/my-mapping": {},
  "/dashboard/api/profile": {},
  "/dashboard/api/environments/options": { environments: [], default_slug: "default" },
  "/dashboard/api/options": {
    models: [MODEL],
    default_agent_model: MODEL.id,
    default_agent_reasoning_effort: "high",
    default_agent_subagent_model: MODEL.id,
    default_agent_subagent_reasoning_effort: "high",
  },
  "/dashboard/api/repos": { installations: [], repositories: [] },
  "/dashboard/api/skills": { items: [], next_offset: null },
}

function send(response, status, body, headers = {}) {
  const payload = typeof body === "string" ? body : JSON.stringify(body)
  response.writeHead(status, { "content-type": "application/json", ...headers })
  response.end(payload)
}

const server = http.createServer(async (request, response) => {
  const url = new URL(request.url, `http://127.0.0.1:${port}`)
  const cookie = request.headers.cookie || "—"
  console.log(`${request.method} ${url.pathname}${url.search}  cookie: ${cookie}`)

  if (url.pathname === "/dashboard/api/auth/login") {
    const challenge = url.searchParams.get("desktop_handoff")
    // Mirror the backend: only a port number crosses the wire, so the redirect
    // target can never be steered off loopback.
    const loopback = Number(url.searchParams.get("desktop_port"))
    if (!challenge || !Number.isInteger(loopback) || loopback < 1024 || loopback > 65535) {
      console.log("  ✗ login is missing a valid desktop_handoff/desktop_port")
      return send(response, 400, { error: "not a desktop login" })
    }
    const code = randomBytes(24).toString("base64url")
    handoffs.set(code, { challenge, session: `stub-session-${randomBytes(6).toString("hex")}` })
    const target = `http://127.0.0.1:${loopback}/callback?code=${code}`
    console.log(`  → ${target}`)
    response.writeHead(302, { location: target })
    return response.end()
  }

  if (url.pathname === "/dashboard/api/auth/desktop/exchange" && request.method === "POST") {
    const chunks = []
    for await (const chunk of request) chunks.push(chunk)
    const { code, verifier } = JSON.parse(Buffer.concat(chunks).toString() || "{}")
    const pending = handoffs.get(code)
    if (!pending) {
      console.log("  ✗ unknown handoff code")
      return send(response, 400, { error: "unknown handoff code" })
    }
    handoffs.delete(code)
    if (createHash("sha256").update(verifier || "").digest("base64url") !== pending.challenge) {
      console.log("  ✗ verifier does not match the challenge")
      return send(response, 400, { error: "verifier mismatch" })
    }
    console.log("  ✓ verifier matched — handing back a session")
    return send(response, 200, { session: pending.session, expires_in: 3600 })
  }

  const signedIn = (request.headers.cookie || "").includes("osw_session=")
  if (!signedIn) {
    if (url.pathname === "/dashboard/api/me") console.log("  ✗ /me without a session cookie")
    return send(response, 401, { detail: "not authenticated" })
  }
  if (url.pathname === "/dashboard/api/me") {
    console.log("  ✓ /me carried the session cookie — desktop login works")
  }
  send(response, 200, SIGNED_IN_ROUTES[url.pathname] ?? [])
})

server.listen(port, "127.0.0.1", () => {
  console.log(`stub backend on http://127.0.0.1:${port}`)
})
