import { ChatContextWindowPopover } from "@langchain/macaw-components/ChatContextWindowPopover"
import { Text } from "@langchain/macaw-components/Text"

import { formatCost } from "@/features/agents/lib/contextUsage"

export interface ContextWindowMeterProps {
  usedTokens?: number | null
  contextWindow?: number | null
  model?: string | null
  costUsd?: number | null
}

export function ContextWindowMeter({
  usedTokens,
  contextWindow,
  model,
  costUsd,
}: ContextWindowMeterProps) {
  if (usedTokens == null) return null

  return (
    <ChatContextWindowPopover
      tokensUsed={usedTokens}
      contextWindow={contextWindow}
      model={model ?? undefined}
      warningThreshold={0.9}
      className="context-window-meter"
      footer={
        costUsd != null ? (
          <div className="flex items-baseline justify-between gap-space-4">
            <Text variant="xs" color="secondary">
              Thread cost
            </Text>
            <Text variant="xs" className="tabular-nums">
              {formatCost(costUsd)}
            </Text>
          </div>
        ) : null
      }
    />
  )
}
