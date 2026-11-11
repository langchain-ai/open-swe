import * as Crypto from "expo-crypto"
import * as WebBrowser from "expo-web-browser"

import OpenSWEAuth from "../../modules/open-swe-auth"
import { APIClient, APIError } from "./apiClient"
import { stringAt } from "./models"

export class SignInCancelled extends Error {
  constructor() {
    super("Sign-in was cancelled.")
  }
}

function base64URL(base64: string): string {
  return base64.replaceAll("+", "-").replaceAll("/", "_").replaceAll("=", "")
}

const BASE64 =
  "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"

function bytesToBase64(bytes: Uint8Array): string {
  let out = ""
  for (let i = 0; i < bytes.length; i += 3) {
    const a = bytes[i]
    const b = bytes[i + 1] ?? 0
    const c = bytes[i + 2] ?? 0
    out += BASE64[a >> 2] + BASE64[((a & 3) << 4) | (b >> 4)]
    out += i + 1 < bytes.length ? BASE64[((b & 15) << 2) | (c >> 6)] : "="
    out += i + 2 < bytes.length ? BASE64[c & 63] : "="
  }
  return out
}

/**
 * Signs in to a backend running under `langgraph dev`, the way the local web
 * dashboard does: `GET /auth/dev-login` signs in as the `gh` CLI's user. The
 * server refuses the route outside `langgraph dev`, and the app only tries it
 * for a backend on this machine. Resolves with null when it is not offered.
 */
export async function devSignIn(backend: string): Promise<string | null> {
  if (!APIClient.isLocalOrigin(backend)) return null
  return await OpenSWEAuth.devSignIn(backend)
}

/**
 * Signs in through the dashboard's desktop handoff, the flow the CLI and
 * desktop app use.
 *
 * The app opens the dashboard login in a browser sheet with a PKCE challenge
 * and a loopback port. After login the backend redirects the browser to
 * `http://127.0.0.1:<port>/callback?code=…`, which the native listener answers;
 * the app then trades the code and its verifier for a session. The verifier
 * never leaves the app.
 */
export async function browserSignIn(backend: string): Promise<string> {
  const verifier = base64URL(bytesToBase64(Crypto.getRandomBytes(32)))
  const challenge = base64URL(
    await Crypto.digestStringAsync(
      Crypto.CryptoDigestAlgorithm.SHA256,
      verifier,
      {
        encoding: Crypto.CryptoEncoding.BASE64,
      }
    )
  )

  // Anything on this device can reach the loopback port, so a code that arrives
  // there is only a candidate: it signs in only if it redeems with this flow's
  // verifier. One that does not is skipped, and the flow keeps waiting for the
  // browser's own redirect until the sheet is closed.
  let settle: (token: string | null) => void = () => {}
  const result = new Promise<string | null>((resolve) => {
    settle = resolve
  })
  const subscription = OpenSWEAuth.addListener("onCode", ({ code }) => {
    exchange(backend, code, verifier).then(
      (token) => {
        WebBrowser.dismissAuthSession()
        settle(token)
      },
      (error: unknown) => {
        if (!(error instanceof APIError && error.status === 400)) {
          WebBrowser.dismissAuthSession()
          settle(null)
        }
      }
    )
  })

  try {
    const port = await OpenSWEAuth.startLoopback()
    const query = `desktop=1&desktop_handoff=${encodeURIComponent(challenge)}&desktop_port=${port}`
    // The flow ends on the loopback URL, never on this scheme, so the session
    // only resolves on its own when the sheet is dismissed.
    WebBrowser.openAuthSessionAsync(
      `${backend}/dashboard/api/auth/login?${query}`,
      "openswe://signed-in"
    ).then(
      () => settle(null),
      () => settle(null)
    )
    const token = await result
    if (!token) throw new SignInCancelled()
    return token
  } finally {
    subscription.remove()
    OpenSWEAuth.stopLoopback()
  }
}

async function exchange(
  backend: string,
  code: string,
  verifier: string
): Promise<string> {
  const response = await fetch(
    `${backend}/dashboard/api/auth/desktop/exchange`,
    {
      method: "POST",
      credentials: "omit",
      headers: {
        "Content-Type": "application/json",
        Accept: "application/json",
        Origin: backend,
      },
      body: JSON.stringify({ code, verifier }),
    }
  )
  const text = await response.text()
  let json: unknown = null
  try {
    json = JSON.parse(text)
  } catch {
    // An error page; the status is enough.
  }
  if (!response.ok)
    throw new APIError(
      response.status,
      stringAt(json, "detail") ?? "sign-in exchange failed"
    )
  const token = stringAt(json, "session")
  if (!token)
    throw new APIError(0, "The server sent a malformed sign-in response.")
  return token
}
