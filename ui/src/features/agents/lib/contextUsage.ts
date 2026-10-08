import { AIMessage } from "@langchain/core/messages"
import type { BaseMessage } from "@langchain/core/messages"

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

export function formatTokenCount(count: number): string {
  if (!Number.isFinite(count) || count <= 0) return "0"
  if (count >= 1_000_000) return `${(count / 1_000_000).toFixed(1)}M`
  if (count >= 1_000) return `${(count / 1_000).toFixed(1)}K`
  return String(Math.round(count))
}
