#!/usr/bin/env node
// Vite dev server, hot reload and all, against a deployed backend.
//
// The deployment sets `osw_session` on its own origin and rejects mutations
// carrying any other Origin, so a dev server on localhost can neither read a
// session nor obtain one through the browser. This mints one out of band with
// the same PKCE loopback handoff the desktop app uses: the GitHub leg still
// runs against the deployment's own registered callback, and only the final
// short-lived code crosses to 127.0.0.1.

import { spawn } from "node:child_process"
import { createHash, randomBytes } from "node:crypto"
import { mkdirSync, readFileSync, writeFileSync } from "node:fs"
import http from "node:http"
import { homedir } from "node:os"
import path from "node:path"
import { fileURLToPath } from "node:url"

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..")
const ENV_FILE = path.join(ROOT, ".env")
const CACHE_FILE = path.join(
  homedir(),
  ".cache",
  "open-swe",
  "dev-session.json"
)
const LOGIN_TIMEOUT_MS = 5 * 60 * 1000
// The origin the backend allows a native client to redeem a handoff from; the
// desktop app sends the same one. Node sends no Origin of its own, and the
// exchange is a POST, so without this the CSRF check rejects it.
const HANDOFF_ORIGIN = "open-swe://app"
// Re-login rather than hand Vite a session that dies mid-afternoon.
const MIN_REMAINING_MS = 60 * 60 * 1000

function devServerUrl() {
  return `http://localhost:${process.env.PORT || 3000}`
}

function fail(message) {
  process.stderr.write(`dev-prod: ${message}\n`)
  process.exit(1)
}

function log(message) {
  process.stderr.write(`dev-prod: ${message}\n`)
}

function readCache() {
  try {
    const parsed = JSON.parse(readFileSync(CACHE_FILE, "utf8"))
    if (
      typeof parsed?.url !== "string" ||
      typeof parsed?.session !== "string"
    ) {
      return null
    }
    return parsed
  } catch {
    return null
  }
}

function writeCache(entry) {
  mkdirSync(path.dirname(CACHE_FILE), { recursive: true })
  writeFileSync(CACHE_FILE, `${JSON.stringify(entry, null, 2)}\n`, {
    mode: 0o600,
  })
}

/** Milliseconds until a session JWT expires, or 0 when it is unreadable. */
function remainingMs(session) {
  try {
    const [, payload] = session.split(".")
    const { exp } = JSON.parse(Buffer.from(payload, "base64url").toString())
    return typeof exp === "number" ? exp * 1000 - Date.now() : 0
  } catch {
    return 0
  }
}

function normalizeBackendUrl(value) {
  const url = new URL(value.trim())
  if (url.protocol !== "http:" && url.protocol !== "https:") {
    throw new Error("must use http or https")
  }
  return url.origin
}

/**
 * `DASHBOARD_API_URL` as `.env` sets it. Only this one key is read: the rest of
 * that file is the local backend's secrets, which have no business in the
 * environment of a dev server the browser talks to.
 */
function backendUrlFromEnvFile() {
  let contents
  try {
    contents = readFileSync(ENV_FILE, "utf8")
  } catch {
    return undefined
  }
  // Last assignment wins, the way sourcing the file would resolve it.
  for (const line of contents.split("\n").reverse()) {
    const match = /^\s*(?:export\s+)?DASHBOARD_API_URL\s*=\s*(.*)$/.exec(line)
    if (match) return match[1].trim().replace(/^(['"])(.*)\1$/, "$2")
  }
  return undefined
}

function resolveBackendUrl() {
  const configured =
    process.env.DASHBOARD_API_URL?.trim() || backendUrlFromEnvFile()
  if (!configured) {
    fail(
      "no DASHBOARD_API_URL. Add the backend to develop against to .env:\n" +
        "  DASHBOARD_API_URL=https://your-deployment.example.com\n" +
        "There is no default: a fallback would be someone else's production."
    )
  }
  try {
    return normalizeBackendUrl(configured)
  } catch (error) {
    return fail(`DASHBOARD_API_URL ${error.message}`)
  }
}

function openBrowser(url) {
  const [command, args] =
    process.platform === "darwin"
      ? ["open", [url]]
      : process.platform === "win32"
        ? // Not `cmd /c start`: cmd would read the login URL's `&` as a command
          // separator and drop everything from `desktop_port` onwards.
          ["rundll32", ["url.dll,FileProtocolHandler", url]]
        : ["xdg-open", [url]]
  const child = spawn(command, args, { stdio: "ignore", detached: true })
  child.on("error", () => {})
  child.unref()
}

function page(heading, detail, script = "") {
  return `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Open SWE</title>
<style>
  :root { color-scheme: light dark }
  body {
    font: 16px/1.5 system-ui, -apple-system, sans-serif;
    margin: 0; min-height: 100vh;
    display: flex; flex-direction: column;
    align-items: center; justify-content: center;
    text-align: center; padding: 2rem;
  }
  h1 { font-size: 1.25rem; margin: 0 0 .5rem }
  p { margin: 0; opacity: .7 }
</style>
</head>
<body>
<h1>${heading}</h1>
<p id="detail">${detail}</p>
${script}
</body>
</html>
`
}

// This tab lands here before Vite is listening — the session it just handed
// back is what the dev server is started with. So wait for the port rather than
// redirecting into a closed one. `no-cors` makes the probe a liveness check the
// browser will not block; its opaque response is never read.
const SIGNED_IN_PAGE = page(
  "You're signed in",
  "Waiting for the dev server…",
  `<script>
const target = ${JSON.stringify(devServerUrl())}
;(async () => {
  for (let attempt = 0; attempt < 240; attempt++) {
    try {
      await fetch(target, { mode: "no-cors", cache: "no-store" })
      location.replace(target)
      return
    } catch {
      await new Promise((resolve) => setTimeout(resolve, 500))
    }
  }
  document.getElementById("detail").innerHTML =
    'The dev server did not come up. <a href="' + target + '">' + target + '</a>'
})()
</script>`
)

const FAILED_PAGE = page(
  "Sign-in failed",
  "No sign-in code arrived. Try again from your terminal."
)

/** Bind a loopback listener for one browser handoff and resolve its code. */
async function awaitHandoffCode() {
  let resolveCode = () => {}
  const code = new Promise((resolve) => {
    resolveCode = resolve
  })

  const server = http.createServer((request, response) => {
    const url = new URL(request.url, "http://127.0.0.1")
    if (url.pathname !== "/callback") {
      response.writeHead(404).end()
      return
    }
    const value = url.searchParams.get("code")
    response.writeHead(200, { "content-type": "text/html; charset=utf-8" })
    response.end(value ? SIGNED_IN_PAGE : FAILED_PAGE, () =>
      finish(value || null)
    )
  })

  let timer = null
  function finish(value) {
    if (timer) clearTimeout(timer)
    timer = null
    server.closeAllConnections()
    server.close()
    resolveCode(value)
  }

  await new Promise((resolve, reject) => {
    server.once("error", reject)
    server.listen(0, "127.0.0.1", () => {
      server.removeListener("error", reject)
      resolve()
    })
  })
  timer = setTimeout(() => finish(null), LOGIN_TIMEOUT_MS)

  return { port: server.address().port, code }
}

async function login(backendUrl) {
  const verifier = randomBytes(32).toString("base64url")
  const challenge = createHash("sha256").update(verifier).digest("base64url")
  const { port, code } = await awaitHandoffCode()

  const loginUrl = new URL("/dashboard/api/auth/login", backendUrl)
  loginUrl.searchParams.set("desktop_handoff", challenge)
  loginUrl.searchParams.set("desktop_port", String(port))

  log(`signing in to ${backendUrl} — approve in your browser`)
  log(loginUrl.toString())
  openBrowser(loginUrl.toString())

  const handoffCode = await code
  if (!handoffCode) fail("no sign-in code received")

  const response = await fetch(
    new URL("/dashboard/api/auth/desktop/exchange", backendUrl),
    {
      method: "POST",
      headers: {
        "content-type": "application/json",
        origin: HANDOFF_ORIGIN,
      },
      body: JSON.stringify({ code: handoffCode, verifier }),
    }
  )
  if (!response.ok) {
    fail(`session exchange failed: ${response.status} ${await response.text()}`)
  }
  const { session } = await response.json()
  if (typeof session !== "string" || !session)
    fail("session exchange returned no session")
  return session
}

async function main() {
  const backendUrl = resolveBackendUrl()
  const cached = readCache()

  let session =
    cached?.url === backendUrl && remainingMs(cached.session) > MIN_REMAINING_MS
      ? cached.session
      : null
  if (!session) {
    session = await login(backendUrl)
    writeCache({ url: backendUrl, session })
  }

  const days = Math.round(remainingMs(session) / 86_400_000)
  log(`serving ${devServerUrl()} against ${backendUrl}`)
  log(
    `this is a real session (${days}d left) — runs you start and settings you change are real`
  )

  const child = spawn("pnpm", ["run", "dev"], {
    cwd: ROOT,
    stdio: "inherit",
    env: {
      ...process.env,
      DASHBOARD_API_URL: backendUrl,
      OPEN_SWE_DEV_SESSION: session,
    },
  })
  child.on("exit", (codeValue, signal) => {
    process.exit(signal ? 1 : (codeValue ?? 0))
  })
}

await main()
