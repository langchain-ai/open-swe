import { AIMessage } from "@langchain/core/messages"
import type { BaseMessage } from "@langchain/core/messages"

interface UsageMetadata {
  input_tokens?: unknown
  output_tokens?: unknown
  total_tokens?: unknown
}

function tokenValue(value: unknown): number {
  return typeof value === "number" && Number.isFinite(value) && value > 0
    ? value
    : 0
}

export function contextTokensFromUsageMetadata(usage: unknown): number | null {
  if (!usage || typeof usage !== "object") return null
  const metadata = usage as UsageMetadata
  const input = tokenValue(metadata.input_tokens)
  const output = tokenValue(metadata.output_tokens)
  if (input || output) return input + output
  const total = tokenValue(metadata.total_tokens)
  return total || null
}

export function latestContextTokens(
  messages: ReadonlyArray<BaseMessage>
): number | null {
  for (let i = messages.length - 1; i >= 0; i -= 1) {
    const message = messages[i]
    if (!message || !AIMessage.isInstance(message)) continue
    const usage = (message as unknown as { usage_metadata?: unknown })
      .usage_metadata
    const tokens = contextTokensFromUsageMetadata(usage)
    if (tokens != null) return tokens
    return null
  }
  return null
}

export function formatTokenCount(count: number): string {
  if (!Number.isFinite(count) || count <= 0) return "0"
  if (count >= 1_000_000) return `${(count / 1_000_000).toFixed(1)}M`
  if (count >= 1_000) return `${(count / 1_000).toFixed(1)}K`
  return String(Math.round(count))
}
