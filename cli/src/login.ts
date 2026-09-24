import { createHash, randomBytes } from "node:crypto"

import { exchangeDesktopHandoff } from "./api.ts"
import { errorMessage } from "./json.ts"

const LOGIN_TIMEOUT_MS = 5 * 60 * 1000

function page(heading: string, detail: string): string {
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
<p>${detail}</p>
</body>
</html>
`
}

const SIGNED_IN_PAGE = page(
  "You're signed in",
  "You can close this tab and return to your terminal."
)
const FAILED_PAGE = page(
  "Sign-in failed",
  "Open SWE did not receive a sign-in code. Try `oswe login` again."
)

function openBrowser(url: string): void {
  const command =
    process.platform === "darwin"
      ? ["open", url]
      : process.platform === "win32"
        ? ["cmd", "/c", "start", "", url]
        : ["xdg-open", url]
  try {
    Bun.spawn(command, { stdout: "ignore", stderr: "ignore" }).unref()
  } catch (cause) {
    process.stderr.write(
      `oswe: could not open a browser (${errorMessage(cause)}). Open the URL above manually.\n`
    )
  }
}

/**
 * Run the desktop handoff login in the user's own browser and return the
 * session JWT. The PKCE verifier never leaves this process.
 */
export async function login(backend: string): Promise<string> {
  const verifier = randomBytes(32).toString("base64url")
  const challenge = createHash("sha256").update(verifier).digest("base64url")

  let settle: (code: string | null) => void = () => {}
  const received = new Promise<string | null>((resolve) => {
    settle = resolve
  })

  const server = Bun.serve({
    hostname: "127.0.0.1",
    port: 0,
    fetch(request) {
      const url = new URL(request.url)
      if (url.pathname !== "/callback")
        return new Response(null, { status: 404 })
      const code = url.searchParams.get("code")
      const response = new Response(code ? SIGNED_IN_PAGE : FAILED_PAGE, {
        headers: { "content-type": "text/html; charset=utf-8" },
      })
      // Settle after handing the page back, so stopping the server cannot
      // truncate the browser's response.
      queueMicrotask(() => settle(code))
      return response
    },
  })

  const query = new URLSearchParams({
    desktop: "1",
    desktop_handoff: challenge,
    desktop_port: String(server.port),
  })
  const loginUrl = `${backend}/dashboard/api/auth/login?${query.toString()}`
  process.stdout.write(`Opening ${loginUrl}\n`)
  openBrowser(loginUrl)

  const timeout = setTimeout(() => settle(null), LOGIN_TIMEOUT_MS)
  let code: string | null
  try {
    code = await received
  } finally {
    clearTimeout(timeout)
    await server.stop()
  }
  if (code === null) {
    throw new Error("login timed out or was cancelled")
  }
  return await exchangeDesktopHandoff(backend, code, verifier)
}
