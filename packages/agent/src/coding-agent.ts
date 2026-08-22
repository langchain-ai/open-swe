import { ChatOpenAI } from "@langchain/openai"
import {
  createDeepAgent,
  type BackendFactory,
  type CreateDeepAgentParams,
} from "deepagents"

import { createLocalWorkspaceBackend } from "@open-swe/workspace"

const DEFAULT_MODEL_ID = "openai:gpt-5.6-sol"
const OPENAI_EFFORTS = ["none", "low", "medium", "high", "xhigh"] as const
const CHATGPT_API_BASE_URL = "https://chatgpt.com/backend-api/codex"
type OpenAIEffort = (typeof OPENAI_EFFORTS)[number]

function isOpenAIEffort(value: unknown): value is OpenAIEffort {
  return (
    typeof value === "string" &&
    (OPENAI_EFFORTS as readonly string[]).includes(value)
  )
}

interface OAuthBrokerCredentials {
  access_token: string
  account_id: string
}

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
    const credentials = (await credentialResponse.json()) as Partial<OAuthBrokerCredentials>
    if (!credentials.access_token || !credentials.account_id) {
      throw new Error("The local OpenAI session returned incomplete credentials")
    }
    const request = new Request(input, init)
    const headers = new Headers(request.headers)
    headers.set("authorization", `Bearer ${credentials.access_token}`)
    headers.set("chatgpt-account-id", credentials.account_id)
    headers.set("originator", "open_swe_desktop")
    return fetchImpl(new Request(request, { headers }))
  }
}

export interface CodingAgentConfig {
  configurable?: Record<string, unknown>
}

export interface CodingAgentDependencies {
  model: NonNullable<CreateDeepAgentParams["model"]>
  backend?: CreateDeepAgentParams["backend"] | BackendFactory
}

export function resolveCodingModel(configurable: Record<string, unknown> = {}) {
  const requested = configurable.agent_model_id
  const modelId =
    typeof requested === "string" && requested.startsWith("openai:")
      ? requested
      : DEFAULT_MODEL_ID
  const requestedEffort = configurable.agent_effort
  const effort: OpenAIEffort = isOpenAIEffort(requestedEffort)
    ? requestedEffort
    : "high"

  const oauthFetch = createOpenAiOAuthFetch(process.env)
  return new ChatOpenAI({
    model: modelId.slice("openai:".length),
    apiKey: process.env.OPENAI_API_KEY || (oauthFetch ? "oauth" : undefined),
    ...(oauthFetch
      ? {
          configuration: {
            baseURL: CHATGPT_API_BASE_URL,
            fetch: oauthFetch,
          },
        }
      : {}),
    useResponsesApi: true,
    zdrEnabled: true,
    modelKwargs: {
      include: ["reasoning.encrypted_content"],
    },
    reasoning: { effort, summary: "auto" },
    timeout: 15 * 60 * 1_000,
  })
}

const SYSTEM_PROMPT = `You are a coding agent working directly in a local repository selected by the user.

Inspect existing files before changing them. Make focused edits, run the repository's relevant tests or checks, and iterate until the requested outcome is complete. Treat the repository root as /. Never attempt to access paths outside it. Do not expose environment variables or credentials. Avoid destructive commands unless the user explicitly asks for them. Summarize the result and any unresolved issue when finished.`

export function createCodingAgentGraph({
  model,
  backend,
}: CodingAgentDependencies): ReturnType<typeof createDeepAgent> {
  // Deep Agents supports async backend factories at runtime, but the public
  // createDeepAgent parameter type currently narrows factories to sync returns.
  const graphBackend = backend as CreateDeepAgentParams["backend"]
  return createDeepAgent({
    model,
    systemPrompt: SYSTEM_PROMPT,
    ...(graphBackend ? { backend: graphBackend } : {}),
  })
}

export async function createCodingAgent(
  config: CodingAgentConfig = {}
): Promise<ReturnType<typeof createDeepAgent>> {
  const configurable = config.configurable ?? {}

  return createCodingAgentGraph({
    model: resolveCodingModel(configurable),
    backend: createLocalWorkspaceBackend(),
  })
}
