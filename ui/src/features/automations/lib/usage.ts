import type { AgentSchedule } from "@/features/agents/lib/types"

const TOKENS = new Intl.NumberFormat(undefined, {
  notation: "compact",
  maximumFractionDigits: 1,
})

/**
 * A personal automation's busiest thread today against its daily token cap,
 * e.g. "Fast · 12.3M / 30M tokens today"; null for a system automation.
 */
export function describeUsage(schedule: AgentSchedule): string | null {
  if (!schedule.restricted || schedule.dailyTokenCap == null) return null
  const used = TOKENS.format(schedule.usageToday?.tokens ?? 0)
  return `Fast · ${used} / ${TOKENS.format(schedule.dailyTokenCap)} tokens today`
}
