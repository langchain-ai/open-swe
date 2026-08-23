import { z } from "zod"

import {
  RESPONSES_LITE_HEADER,
  adaptCodexPayload,
  requiresResponsesLite,
} from "./payload.js"

export const CHATGPT_API_BASE_URL = "https://chatgpt.com/backend-api/codex"

/**
 * Codex serves its model allowlist per originator; an unrecognized value gets a
 * stale list that no longer carries current models.
 */
export const CODEX_ORIGINATOR =
  process.env.OPEN_SWE_CODEX_ORIGINATOR || "langchain"

/** What the loopback broker returns; validated because it crosses a process boundary. */
const BrokerCredentials = z.object({
  access_token: z.string().min(1),
  account_id: z.string().min(1),
})

function oauthBrokerConfig(env: NodeJS.ProcessEnv): {
  url: URL
  token: string
} | null {
  const rawUrl = env.OPEN_SWE_OPENAI_OAUTH_BROKER_URL
  const token = env.OPEN_SWE_OPENAI_OAUTH_BROKER_TOKEN
  if (!rawUrl || !token) return null
  const url = new URL(rawUrl)
  if (
    url.protocol !== "http:" ||
    url.hostname !== "127.0.0.1" ||
    url.pathname !== "/token" ||
    url.username ||
    url.password
  ) {
    throw new Error("The local OpenAI credential broker address is invalid")
  }
  return { url, token }
}

async function readJsonBody(
  request: Request
): Promise<Record<string, unknown> | null> {
  if (!["POST", "PATCH", "PUT"].includes(request.method)) return null
  try {
    const parsed: unknown = await request.clone().json()
    return parsed && typeof parsed === "object" && !Array.isArray(parsed)
      ? (parsed as Record<string, unknown>)
      : null
  } catch {
    return null
  }
}

/**
 * A `fetch` that authenticates against Codex with the desktop's ChatGPT
 * session, fetching a short-lived credential from the loopback broker per
 * request and applying the request shape Codex expects.
 *
 * Returns `null` when no broker is configured, which is how callers detect that
 * the ChatGPT path is unavailable.
 */
export function createOpenAiOAuthFetch(
  env: NodeJS.ProcessEnv,
  fetchImpl: typeof fetch = globalThis.fetch
): typeof fetch | null {
  const broker = oauthBrokerConfig(env)
  if (!broker) return null
  return async (input, init) => {
    const credentialResponse = await fetchImpl(broker.url, {
      headers: { authorization: `Bearer ${broker.token}` },
      signal: init?.signal,
    })
    if (!credentialResponse.ok) {
      throw new Error("The local OpenAI session is unavailable; sign in again")
    }
    const credentials = BrokerCredentials.safeParse(
      await credentialResponse.json()
    )
    if (!credentials.success) {
      throw new Error("The local OpenAI session returned incomplete credentials")
    }
    const request = new Request(input, init)
    const headers = new Headers(request.headers)
    headers.set("authorization", `Bearer ${credentials.data.access_token}`)
    headers.set("chatgpt-account-id", credentials.data.account_id)
    headers.set("originator", CODEX_ORIGINATOR)

    const payload = await readJsonBody(request)
    if (!payload) return fetchImpl(new Request(request, { headers }))
    if (requiresResponsesLite(payload.model)) {
      headers.set(RESPONSES_LITE_HEADER, "true")
    }
    return fetchImpl(
      new Request(request.url, {
        method: request.method,
        headers,
        body: JSON.stringify(adaptCodexPayload(payload)),
        signal: request.signal,
      })
    )
  }
}
