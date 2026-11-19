import { AIMessage } from "@langchain/core/messages"
import type { BaseMessage } from "@langchain/core/messages"
import type { Message } from "@/features/agents/lib/types"
import type { ModelOption } from "@/lib/api"

interface UsageMetadata {
  input_tokens?: unknown
  output_tokens?: unknown
  total_tokens?: unknown
  model?: unknown
}

/** Context size and model of the newest AI message, for the composer's meter. */
export interface ContextUsage {
  tokens: number
  model: string | null
}

export function resolveContextModel(
  models: ReadonlyArray<Pick<ModelOption, "id" | "label" | "context_window">>,
  ...candidates: Array<string | null | undefined>
) {
  for (const id of candidates) {
    if (!id) continue
    const match =
      models.find((model) => model.id === id) ??
      models.find((model) => model.id.slice(model.id.indexOf(":") + 1) === id)
    if (match) return match
  }
  return undefined
}

function tokenValue(value: unknown): number {
  return typeof value === "number" && Number.isFinite(value) && value > 0
    ? value
    : 0
}

export function contextUsageFromUsageMetadata(
  usage: unknown,
  model?: unknown
): ContextUsage | null {
  if (!usage || typeof usage !== "object") return null
  const metadata = usage as UsageMetadata
  const input = tokenValue(metadata.input_tokens)
  const output = tokenValue(metadata.output_tokens)
  const tokens =
    input || output ? input + output : tokenValue(metadata.total_tokens)
  if (!tokens) return null
  const name = model ?? metadata.model
  return { tokens, model: typeof name === "string" && name ? name : null }
}

export function latestContextUsage(
  messages: ReadonlyArray<BaseMessage>
): ContextUsage | null {
  const message = messages.findLast((m) => AIMessage.isInstance(m))
  if (!message) return null
  return contextUsageFromUsageMetadata(
    (message as unknown as { usage_metadata?: unknown }).usage_metadata,
    message.response_metadata?.model_name
  )
}

export function formatCost(usd: number): string {
  return usd > 0 && usd < 0.01 ? "<$0.01" : `$${usd.toFixed(2)}`
}

/** Each run's cost, attributed to the last turn it ran in and summed per turn. */
export function turnCosts(
  messages: ReadonlyArray<Message>,
  runCosts: Readonly<Record<string, number>> = {}
): Map<string, number> {
  const lastTurn = new Map<string, string>()
  for (const message of messages)
    for (const id of message.invocationIds ?? []) lastTurn.set(id, message.id)
  const costs = new Map<string, number>()
  for (const [id, turnId] of lastTurn) {
    const cost = runCosts[id]
    if (cost !== undefined) costs.set(turnId, (costs.get(turnId) ?? 0) + cost)
  }
  return costs
}
