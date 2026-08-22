import { ChatOpenAI } from "@langchain/openai"
import {
  createDeepAgent,
  type BackendFactory,
  type CreateDeepAgentParams,
} from "deepagents"

import {
  createLocalWorkspaceBackend,
  resolveLocalProject,
} from "@open-swe/workspace"

import { CHATGPT_API_BASE_URL, createOpenAiOAuthFetch } from "@open-swe/codex"

import { createLocalSkillsMiddleware } from "./skills.js"

const DEFAULT_MODEL_ID = "openai:gpt-5.6-sol"
const OPENAI_EFFORTS = ["none", "low", "medium", "high", "xhigh"] as const
type OpenAIEffort = (typeof OPENAI_EFFORTS)[number]

function isOpenAIEffort(value: unknown): value is OpenAIEffort {
  return (
    typeof value === "string" &&
    (OPENAI_EFFORTS as readonly string[]).includes(value)
  )
}

export interface CodingAgentConfig {
  configurable?: Record<string, unknown>
}

export interface CodingAgentDependencies {
  model: NonNullable<CreateDeepAgentParams["model"]>
  backend?: CreateDeepAgentParams["backend"] | BackendFactory
  middleware?: CreateDeepAgentParams["middleware"]
}

export interface CodingModelDependencies {
  env?: NodeJS.ProcessEnv
  fetchImpl?: typeof fetch
}

export function resolveCodingModel(
  configurable: Record<string, unknown> = {},
  { env = process.env, fetchImpl }: CodingModelDependencies = {}
) {
  const requested = configurable.agent_model_id
  const modelId =
    typeof requested === "string" && requested.startsWith("openai:")
      ? requested
      : DEFAULT_MODEL_ID
  const requestedEffort = configurable.agent_effort
  const effort: OpenAIEffort = isOpenAIEffort(requestedEffort)
    ? requestedEffort
    : "high"

  // An explicit key wins over the ChatGPT session: the OAuth transport rewrites
  // `Authorization`, so binding it alongside a key would discard the key and
  // silently route to Codex, whose catalog is per-account.
  const apiKey = env.OPENAI_API_KEY
  const oauthFetch = apiKey
    ? null
    : createOpenAiOAuthFetch(env, fetchImpl ?? globalThis.fetch)
  const baseURL =
    env.OPENAI_BASE_URL || (oauthFetch ? CHATGPT_API_BASE_URL : undefined)
  const transport = oauthFetch ?? fetchImpl

  return new ChatOpenAI({
    model: modelId.slice("openai:".length),
    apiKey: apiKey || (oauthFetch ? "oauth" : undefined),
    ...(baseURL || transport
      ? {
          configuration: {
            ...(baseURL ? { baseURL } : {}),
            ...(transport ? { fetch: transport } : {}),
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

Inspect existing files before changing them. Make focused edits, run the repository's relevant tests or checks, and iterate until the requested outcome is complete. Paths are real absolute paths on the user's machine; the selected repository is the working directory that relative paths resolve against. Confine edits to the selected repository unless the user asks otherwise. This is the user's own machine and their real checkout: never modify git configuration, and never set a commit identity — commits use the identity the user has already configured. Do not expose environment variables or credentials. Avoid destructive commands unless the user explicitly asks for them. Summarize the result and any unresolved issue when finished.`

export function createCodingAgentGraph({
  model,
  backend,
  middleware,
}: CodingAgentDependencies): ReturnType<typeof createDeepAgent> {
  // Deep Agents supports async backend factories at runtime, but the public
  // createDeepAgent parameter type currently narrows factories to sync returns.
  const graphBackend = backend as CreateDeepAgentParams["backend"]
  return createDeepAgent({
    model,
    systemPrompt: SYSTEM_PROMPT,
    ...(graphBackend ? { backend: graphBackend } : {}),
    ...(middleware?.length ? { middleware } : {}),
  })
}

function localProject(configurable: Record<string, unknown>): string | null {
  try {
    return resolveLocalProject({
      localProjectPath: configurable.local_project_path,
    })
  } catch {
    return null
  }
}

export async function createCodingAgent(
  config: CodingAgentConfig = {}
): Promise<ReturnType<typeof createDeepAgent>> {
  const configurable = config.configurable ?? {}
  const skills = createLocalSkillsMiddleware(localProject(configurable))

  return createCodingAgentGraph({
    model: resolveCodingModel(configurable),
    backend: createLocalWorkspaceBackend(),
    ...(skills ? { middleware: [skills] } : {}),
  })
}
