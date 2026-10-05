import type {
  AgentSchedule,
  AutomationTriggerConfig,
  GitHubTriggerEvent,
} from "@/features/agents/lib/types"
import { AUTOMATION_EVENT_PROVIDERS } from "@/features/agents/lib/types"
import { describeCron } from "@/features/automations/lib/cron"

export function scheduleCron(schedule?: AgentSchedule): string | null {
  const trigger = schedule?.triggers.find((t) => t.kind === "schedule")
  return trigger?.kind === "schedule" ? trigger.cron : null
}

export function githubEvents(
  schedule?: AgentSchedule
): Array<GitHubTriggerEvent> {
  const trigger = schedule?.triggers.find((t) => t.kind === "github")
  return trigger?.kind === "github" ? trigger.events : []
}

/** The triggers the editor's per-provider state stands for. */
export function buildTriggers(
  cron: string | null,
  events: Array<GitHubTriggerEvent>
): Array<AutomationTriggerConfig> {
  const triggers: Array<AutomationTriggerConfig> = []
  if (cron?.trim()) triggers.push({ kind: "schedule", cron: cron.trim() })
  if (events.length > 0) triggers.push({ kind: "github", events })
  return triggers
}

/** One line per provider, e.g. "GitHub: PR closed, PR merged". */
export function describeTriggers(schedule: AgentSchedule): string {
  const parts = schedule.triggers.map((trigger) => {
    if (trigger.kind === "schedule") return describeCron(trigger.cron)
    const provider = AUTOMATION_EVENT_PROVIDERS[trigger.kind]
    const events = trigger.events.map((event) => provider.events[event])
    return `${provider.label}: ${events.join(", ")}`
  })
  return parts.length > 0 ? parts.join(" · ") : "No trigger"
}
